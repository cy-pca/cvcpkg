--- cvcpkg integration for cpkg (getcpkg.net) build scripts.
--
-- Lets a cpkg.lua project pull a pinned, prebuilt binary from the cvcpkg
-- archive (https://cvcpkg.org) into a project-local prefix instead of building
-- the dependency from source. cpkg keeps its Lua+Ninja build; cvcpkg supplies
-- the reproducible binary package manager underneath.
--
-- Usage in cpkg.lua:
--
--     local cvcpkg = require("cvcpkg")            -- or load it over HTTP, below
--     add_project("myapp")
--     add_dependency(function()
--       local boost = cvcpkg.dependency("boost")  -- installs + wires paths
--       -- boost.include_dirs / boost.lib_dirs / boost.libs are available too
--     end)
--
-- cpkg can also fetch this module over HTTP (its external-script feature):
--
--     local cvcpkg = load(io.popen("curl -fsSL https://cvcpkg.org/cvcpkg.lua"):read("*a"))()
--
-- Requires the `cvcpkg` CLI on PATH (pip install cvcpkg). Set CVCPKG_CPKG_PREFIX
-- to change the default dependency prefix; CVCPKG_SERVER_URL / CVCPKG_TOKEN are
-- honoured by the CLI for private/org packages.

local cvcpkg = {}

cvcpkg.default_prefix = os.getenv("CVCPKG_CPKG_PREFIX") or "cvcpkg_deps"

-- Shell-quote a single argument for POSIX sh / Windows tolerant use.
local function shq(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

--- Resolve and install a cvcpkg package, wiring its paths into the cpkg build.
--
-- @param name  package name, optionally "name==version" (a cvcpkg spec).
-- @param opts  optional table:
--                prefix   – install dir (default: cvcpkg.default_prefix)
--                version  – version spec (alternative to name==version)
--                release  – cvcpkg release tag to pin
--                server   – cvcpkg-server URL
--                token    – bearer token for private/org packages
--                require_signatures – bool
-- @return a table: { prefix, include_dirs, lib_dirs, libs,
--                    pkgconfig_dirs, cmake_dirs, bin_dir }
function cvcpkg.dependency(name, opts)
  opts = opts or {}
  local prefix = opts.prefix or cvcpkg.default_prefix
  local spec = name
  if opts.version and not name:find("==") then
    spec = name .. "==" .. opts.version
  end

  local parts = { "cvcpkg", "cpkg", "deps", shq(spec), "--prefix", shq(prefix), "--format", "lua" }
  if opts.release then parts[#parts + 1] = "--release"; parts[#parts + 1] = shq(opts.release) end
  if opts.server then parts[#parts + 1] = "--server"; parts[#parts + 1] = shq(opts.server) end
  if opts.token then parts[#parts + 1] = "--token"; parts[#parts + 1] = shq(opts.token) end
  if opts.require_signatures then parts[#parts + 1] = "--require-signatures" end
  local cmd = table.concat(parts, " ")

  local handle = io.popen(cmd)
  if not handle then
    error("cvcpkg.dependency: could not run cvcpkg (is it on PATH?)")
  end
  local output = handle:read("*a")
  local ok = handle:close()
  if not ok then
    error("cvcpkg.dependency: cvcpkg failed to resolve '" .. tostring(spec) .. "'")
  end

  local chunk, lerr = load(output)
  if not chunk then
    error("cvcpkg.dependency: could not parse cvcpkg output: " .. tostring(lerr))
  end
  local info = chunk()

  -- Best-effort wiring into the cpkg build. These globals exist when running
  -- inside cpkg; guarded so the module also works standalone (e.g. for tests).
  if type(add_include_dir) == "function" then
    for _, d in ipairs(info.include_dirs) do add_include_dir(d) end
  end
  if type(add_lib_dir) == "function" then
    for _, d in ipairs(info.lib_dirs) do add_lib_dir(d) end
  end
  if type(add_lib) == "function" then
    for _, l in ipairs(info.libs) do add_lib(l) end
  end

  return info
end

return cvcpkg
