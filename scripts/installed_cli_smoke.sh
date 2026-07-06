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

echo "==> Creating isolated venv at ${venv_dir}"
uv venv "${venv_dir}" --python 3.12

echo "==> Installing built wheel (not editable source)"
uv pip install --python "${venv_dir}/bin/python" "${wheel_path}"

docsystem_bin="${venv_dir}/bin/docsystem"
venv_python="${venv_dir}/bin/python"
if [[ ! -x "${docsystem_bin}" ]]; then
  echo "error: installed console script not found at ${docsystem_bin}" >&2
  exit 1
fi

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
