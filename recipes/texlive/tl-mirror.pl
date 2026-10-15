#!/usr/bin/env perl
# recipes/texlive/tl-mirror.pl -- build a local, verified TeX Live repository.
#
# Reads a TeX Live package database (texlive.tlpdb, already checked against the
# pin in build.sh / build.ps1), walks the `depend` closure of the requested
# packages for one TeX Live platform, and downloads the runfile container of
# every package in it from the frozen upstream repository. Each container is
# checked against the sha512 the tlpdb records for it before it is kept, so the
# whole tree is pinned by the one tlpdb hash. install-tl then installs offline
# from the mirror. Documentation and source containers are never fetched.
#
# Core modules only (Digest::SHA, File::Copy, Getopt::Long): this runs under the
# build host's perl on unix and under TeX Live's own tlperl on Windows. Downloads
# go through curl, which every builder platform ships (Windows 10+ included).
#
# usage: perl tl-mirror.pl --tlpdb FILE --base URL --dest DIR --arch TLPLATFORM
#                          [--list-only] PKG...
use strict;
use warnings;
use Digest::SHA;
use File::Copy qw(copy);
use File::Path qw(make_path);
use Getopt::Long;

my ($tlpdb, $base, $dest, $arch, $list_only);
GetOptions(
    'tlpdb=s'   => \$tlpdb,
    'base=s'    => \$base,
    'dest=s'    => \$dest,
    'arch=s'    => \$arch,
    'list-only' => \$list_only,
) or die "bad arguments\n";
die "--tlpdb and --arch are required\n" unless $tlpdb && $arch;
die "--base and --dest are required unless --list-only\n"
    unless $list_only || ($base && $dest);
my @roots = @ARGV or die "no root packages given\n";

# ── parse the tlpdb: blank-line separated blocks of `key value` lines ──────
my %pkg;
{
    open my $fh, '<', $tlpdb or die "cannot read $tlpdb: $!\n";
    my $cur;
    while (my $line = <$fh>) {
        $line =~ s/\r?\n\z//;
        if ($line eq '') { $cur = undef; next; }
        next if $line =~ /^ /;                     # file lists, long descriptions
        my ($k, $v) = split / /, $line, 2;
        $v = '' unless defined $v;
        if ($k eq 'name') { $cur = $pkg{$v} = { depend => [] }; next; }
        next unless $cur;
        if    ($k eq 'depend')            { push @{ $cur->{depend} }, $v; }
        elsif ($k eq 'containerchecksum') { $cur->{sha512} = $v; }
        elsif ($k eq 'containersize')     { $cur->{size} = $v; }
        elsif ($k eq 'revision')          { $cur->{revision} = $v; }
    }
    close $fh;
}
die "no packages parsed from $tlpdb\n" unless %pkg;

# ── dependency closure, expanding foo.ARCH to this platform ──────────────
my (%want, @queue);
for my $r (@roots) {
    die "root package '$r' is not in the tlpdb\n" unless $pkg{$r};
    push @queue, $r;
}
while (defined(my $p = shift @queue)) {
    next if $want{$p}++;
    for my $d (@{ $pkg{$p}{depend} }) {
        next if $d =~ /^(?:setting|opt)_/;         # 00texlive.* options, not packages
        (my $n = $d) =~ s/\.ARCH\z/.$arch/;
        # tlmgr skips binary packages a platform does not have; so do we.
        next unless $pkg{$n};
        push @queue, $n unless $want{$n};
    }
}

my @get = sort grep { defined $pkg{$_}{sha512} } keys %want;
my $total = 0;
$total += ($pkg{$_}{size} || 0) for @get;
printf "tl-mirror: %d packages in the closure, %d containers, %.1f MB to fetch\n",
    scalar(keys %want), scalar(@get), $total / 1e6;
if ($list_only) { print "$_\n" for @get; exit 0; }

# ── mirror ───────────────────────────────────────────────────────────────
make_path("$dest/tlpkg", "$dest/archive");
copy($tlpdb, "$dest/tlpkg/texlive.tlpdb") or die "copy tlpdb: $!\n";

sub sha512_of {
    my $d = Digest::SHA->new(512);
    $d->addfile($_[0], 'b');
    return $d->hexdigest;
}

sub good { my $p = shift; my $f = "$dest/archive/$p.tar.xz";
           return -f $f && sha512_of($f) eq $pkg{$p}{sha512}; }

# One curl per batch of containers, so a connection is reused across ~2300
# small files instead of paying a TLS handshake for each. curl keeps going
# past a failed transfer; whatever is missing or wrong is retried singly below.
my @todo = grep { !good($_) } @get;
printf "tl-mirror: %d already present and verified, %d to download\n",
    @get - @todo, scalar(@todo);
while (my @batch = splice(@todo, 0, 100)) {
    my @cmd = ('curl', '-sSL', '--retry', '4', '--retry-delay', '3', '--fail');
    push @cmd, '-o', "$dest/archive/$_.tar.xz", "$base/archive/$_.tar.xz" for @batch;
    system(@cmd);
}
my $n = 0;
for my $p (@get) {
    unless (good($p)) {
        my $file = "$dest/archive/$p.tar.xz";
        system('curl', '-fsSL', '--retry', '4', '--retry-delay', '3',
               '-o', $file, "$base/archive/$p.tar.xz") == 0
            or die "download failed: $base/archive/$p.tar.xz\n";
        unless (good($p)) {
            my $got = sha512_of($file);
            unlink $file;
            die "sha512 mismatch for $p.tar.xz: tlpdb says $pkg{$p}{sha512}, got $got\n";
        }
    }
    $n++;
}
print "tl-mirror: all $n containers verified against the tlpdb\n";
