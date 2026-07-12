#!/usr/bin/env bash
# Verifies the installed docsystem console script, not `PYTHONPATH=src`.
#
# Builds a wheel of the current checkout, installs it into an isolated venv,
# and runs the installed `docsystem` entry point against a fixture project
# from an unrelated cwd. Every invocation of the installed entry point strips
# ambient Python import overrides (PYTHONPATH, PYTHONHOME) so a caller's
# environment cannot make the check silently import this repository's `src/`
# instead of the installed wheel. Requires no network access beyond a warm uv
# cache and no API credentials. Leaves the repository unchanged.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/docsystem-installed-cli-smoke.XXXXXX")"

cleanup() {
  rm -rf "${work_dir}"
}
trap cleanup EXIT

dist_dir="${work_dir}/dist"
venv_dir="${work_dir}/venv"
project_dir="${work_dir}/consumer-project"

echo "==> Building wheel from ${repo_root}"
uv build --wheel "${repo_root}" --out-dir "${dist_dir}"

wheel_path="$(find "${dist_dir}" -maxdepth 1 -name '*.whl' -print -quit)"
if [[ -z "${wheel_path}" ]]; then
  echo "error: no wheel produced in ${dist_dir}" >&2
  exit 1
fi

echo "==> Verifying built distribution identity and version"
expected_version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${repo_root}/src/docsystem/__init__.py")"
if [[ -z "${expected_version}" ]]; then
  echo "error: could not read __version__ from src/docsystem/__init__.py" >&2
  exit 1
fi
expected_wheel_name="documentation_engine-${expected_version}-py3-none-any.whl"
actual_wheel_name="$(basename "${wheel_path}")"
if [[ "${actual_wheel_name}" != "${expected_wheel_name}" ]]; then
  echo "error: built wheel '${actual_wheel_name}' does not match expected '${expected_wheel_name}'" >&2
  exit 1
fi
echo "    wheel: ${actual_wheel_name}"

echo "==> Creating isolated venv at ${venv_dir}"
uv venv "${venv_dir}" --python 3.12

echo "==> Installing built wheel (not editable source)"
uv pip install --python "${venv_dir}/bin/python" "${wheel_path}"

docsystem_bin="${venv_dir}/bin/docsystem"
docsystem_mcp_bin="${venv_dir}/bin/docsystem-mcp"
venv_python="${venv_dir}/bin/python"
if [[ ! -x "${docsystem_bin}" ]]; then
  echo "error: installed console script not found at ${docsystem_bin}" >&2
  exit 1
fi
if [[ ! -x "${docsystem_mcp_bin}" ]]; then
  echo "error: installed console script not found at ${docsystem_mcp_bin}" >&2
  exit 1
fi

echo "==> Verifying installed distribution metadata"
env -u PYTHONPATH -u PYTHONHOME "${venv_python}" - "${expected_version}" <<'PY'
import sys
from importlib.metadata import metadata

expected_version = sys.argv[1]
meta = metadata("documentation-engine")

if meta["Name"] != "documentation-engine":
    sys.exit(f"error: distribution name is {meta['Name']!r}, expected 'documentation-engine'")
if meta["Version"] != expected_version:
    sys.exit(f"error: distribution version is {meta['Version']!r}, expected {expected_version!r}")
license_field = meta.get("License-Expression") or meta.get("License") or ""
if "MIT" not in license_field:
    sys.exit(f"error: distribution license is {license_field!r}, expected it to contain 'MIT'")
author_field = meta.get("Author") or ""
if "Oleg Synelnykov (Jafa7)" not in author_field:
    sys.exit(f"error: distribution author is {author_field!r}, expected 'Oleg Synelnykov (Jafa7)'")
print(f"    metadata: name={meta['Name']} version={meta['Version']} license={license_field} author={author_field}")
PY

echo "==> Verifying the runtime MCP-extra guidance shipped in the wheel"
mcp_error_output="$(env -u PYTHONPATH -u PYTHONHOME "${docsystem_mcp_bin}" 2>&1)" && {
  echo "error: ${docsystem_mcp_bin} unexpectedly succeeded without the optional 'mcp' dependency installed" >&2
  exit 1
}
case "${mcp_error_output}" in
  *"documentation-engine[mcp]"*) ;;
  *)
    echo "error: docsystem-mcp error message does not reference 'documentation-engine[mcp]': ${mcp_error_output}" >&2
    exit 1
    ;;
esac
echo "    docsystem-mcp correctly refuses to start and references 'documentation-engine[mcp]'"

venv_real="$(cd "${venv_dir}" && pwd -P)"
repo_real="$(cd "${repo_root}" && pwd -P)"

assert_module_resolves_to_venv() {
  local resolved
  resolved="$(env -u PYTHONPATH -u PYTHONHOME "${venv_python}" -c 'import docsystem; print(docsystem.__file__)')"
  case "${resolved}" in
    "${venv_real}"/*) ;;
    *)
      echo "error: docsystem resolved outside the installed venv: ${resolved}" >&2
      exit 1
      ;;
  esac
  case "${resolved}" in
    "${repo_real}"/*)
      echo "error: docsystem resolved inside the repository checkout: ${resolved}" >&2
      exit 1
      ;;
  esac
  echo "    resolved docsystem module: ${resolved}"
}

run_cli_checks() {
  local label="$1"
  rm -rf "${project_dir}"
  mkdir -p "${project_dir}"
  echo "==> Running installed docsystem (${label}) from unrelated cwd: ${project_dir}"
  (
    cd "${project_dir}"
    env -u PYTHONPATH -u PYTHONHOME "${docsystem_bin}" init .
    env -u PYTHONPATH -u PYTHONHOME "${docsystem_bin}" doctor .
    env -u PYTHONPATH -u PYTHONHOME "${docsystem_bin}" show-config .
  )
}

echo "==> Verifying the installed docsystem module resolves under the venv"
assert_module_resolves_to_venv
run_cli_checks "clean environment"

echo "==> Re-running with a hostile PYTHONPATH pointing at the repository src/"
export PYTHONPATH="${repo_root}/src"
assert_module_resolves_to_venv
run_cli_checks "hostile PYTHONPATH=${repo_root}/src"
unset PYTHONPATH

echo "==> Smoke check passed: installed wheel entry point works from an unrelated cwd, sanitizing ambient PYTHONPATH."
