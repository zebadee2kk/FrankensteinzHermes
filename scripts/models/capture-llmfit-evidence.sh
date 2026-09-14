#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
llmfit_bin="${FZH_LLMFIT_BIN:-llmfit}"
evidence_root="${FZH_LLMFIT_EVIDENCE_ROOT:-${repo_root}/evidence}"
stamp="${FZH_LLMFIT_EVIDENCE_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
limit="${FZH_LLMFIT_LIMIT:-10}"
out_dir="${evidence_root%/}/llmfit/${stamp}"

case "$limit" in
  ''|*[!0-9]*) echo "FZH_LLMFIT_LIMIT must be an integer" >&2; exit 2 ;;
esac
(( limit >= 1 && limit <= 50 )) || { echo "FZH_LLMFIT_LIMIT must be between 1 and 50" >&2; exit 2; }

command -v "$llmfit_bin" >/dev/null 2>&1 || { echo "llmfit not found: $llmfit_bin" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

umask 077
mkdir -p "$out_dir"

# Never expose repository/provider credentials to the hardware-sizing tool. The
# empty OAuth client id also disables llmfit's interactive GitHub device flow.
run_llmfit() {
  env \
    -u GITHUB_TOKEN -u GH_TOKEN \
    -u OPENROUTER_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
    -u GEMINI_API_KEY -u GROQ_API_KEY -u NVIDIA_API_KEY \
    -u LITELLM_MASTER_KEY \
    LLMFIT_GH_CLIENT_ID= \
    "$llmfit_bin" "$@"
}

global_args=()
[[ -n "${FZH_LLMFIT_RAM:-}" ]] && global_args+=("--ram=${FZH_LLMFIT_RAM}")
[[ -n "${FZH_LLMFIT_CPU_CORES:-}" ]] && global_args+=("--cpu-cores=${FZH_LLMFIT_CPU_CORES}")
[[ -n "${FZH_LLMFIT_MEMORY:-}" ]] && global_args+=("--memory=${FZH_LLMFIT_MEMORY}")

synthetic=false
((${#global_args[@]} > 0)) && synthetic=true

run_llmfit --version > "${out_dir}/llmfit-version.txt" 2>&1
run_llmfit "${global_args[@]}" system --json > "${out_dir}/system.json"

# doctor is useful raw evidence, but it is diagnostic rather than a selection
# gate. Preserve its exit status instead of losing the rest of the bundle if an
# optional host utility is unavailable.
set +e
run_llmfit doctor > "${out_dir}/doctor.txt" 2>&1
doctor_rc=$?
set -e
printf '%s\n' "$doctor_rc" > "${out_dir}/doctor.exit-code"

for use_case in general coding reasoning; do
  run_llmfit "${global_args[@]}" recommend --json --use-case "$use_case" --limit "$limit" \
    > "${out_dir}/recommend-${use_case}.json"
done

python3 "${repo_root}/scripts/models/build-llmfit-report.py" \
  --bundle "$out_dir" \
  --synthetic "$synthetic"

# Fail closed if the evidence unexpectedly contains credential material or
# names of credential-bearing variables. Raw llmfit hardware output should not.
if grep -RIEq --binary-files=without-match \
  'OPENROUTER_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GH_TOKEN|LITELLM_MASTER_KEY|ghp_[A-Za-z0-9]|github_pat_|sk-[A-Za-z0-9]{16,}' \
  "$out_dir"; then
  echo "credential-like material detected in llmfit evidence bundle" >&2
  exit 1
fi

printf '%s\n' "$out_dir"
