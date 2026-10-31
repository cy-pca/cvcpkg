#!/usr/bin/env bash
# recipes/mitsuba-cp313/variants.sh -- the Mitsuba variants this recipe compiles
# (MI_DEFAULT_VARIANTS), sourced by build.sh and test.sh so both agree.
#
#   scalar_rgb, scalar_spectral       CPU, no JIT; scalar_rgb is what the
#                                     mitsuba-blender add-on loads
#   llvm_ad_rgb, llvm_ad_mono_polarized
#                                     CPU JIT through libLLVM (llvm20);
#                                     llvm_ad_mono_polarized is Sionna RT's
#                                     CPU variant
#   cuda_ad_rgb, cuda_ad_mono_polarized   linux: NVIDIA GPU, driver loaded at
#                                     run time (no CUDA toolkit at build time)
#   metal_ad_rgb, metal_ad_mono_polarized macOS arm64: Apple GPU
#
# Every variant compiles every plugin once more, so the list is kept to what
# the consumers (Blender's add-on, Sionna RT) use.
mi_variants() {
    local base="scalar_rgb,scalar_spectral,llvm_ad_rgb,llvm_ad_mono_polarized"
    case "${CVC_PLATFORM}:$(uname -m)" in
        linux:*) echo "${base},cuda_ad_rgb,cuda_ad_mono_polarized" ;;
        macos:arm64) echo "${base},metal_ad_rgb,metal_ad_mono_polarized" ;;
        *) echo "${base}" ;;
    esac
}
