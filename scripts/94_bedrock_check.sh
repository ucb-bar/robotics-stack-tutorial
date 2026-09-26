#!/usr/bin/env bash
# Seat check: can this machine reach the tutorial model with its key?
#
#   scripts/94_bedrock_check.sh            uses AWS_BEARER_TOKEN_BEDROCK etc. if exported,
#                                          else ~/.config/iiswc/bedrock.env (or $IISWC_BEDROCK_ENV)
#
# Needs only bash and curl (no repo, AWS CLI or jq): 93_bedrock_key.sh distribute streams this
# file over ssh to seats that may lack them. Prints nothing secret. Exits 0 only if the model answers.
set -uo pipefail

ENV_FILE="${IISWC_BEDROCK_ENV:-$HOME/.config/iiswc/bedrock.env}"
if [ -z "${AWS_BEARER_TOKEN_BEDROCK:-}" ] && [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

ok()  { printf '  \033[1;32mok\033[0m    %-18s %s\n' "$1" "${2-}"; }
bad() { printf '  \033[1;31mMISS\033[0m  %-18s %s\n' "$1" "${2-}"; exit 1; }

[ -n "${AWS_BEARER_TOKEN_BEDROCK:-}" ] || bad "bedrock key" "not set and $ENV_FILE missing; ask the instructor"
[ -n "${AWS_REGION:-}" ] || bad "AWS_REGION" "missing from $ENV_FILE (ModelBlaster would default to us-east-1)"
[ -n "${MODEL:-}" ]      || bad "MODEL" "missing from $ENV_FILE"
command -v curl >/dev/null || bad "curl" "not installed"
if [ -f "$ENV_FILE" ]; then
  perm=$(stat -c %a "$ENV_FILE" 2>/dev/null || echo "?")
  [ "$perm" = 600 ] || printf '  \033[1;33mwarn\033[0m  %-18s %s\n' "env file mode" "$perm (expected 600): chmod 600 $ENV_FILE"
fi

hdr=$(mktemp); body=$(mktemp); trap 'rm -f "$hdr" "$body"' EXIT
chmod 600 "$hdr"
printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$AWS_BEARER_TOKEN_BEDROCK" > "$hdr"
res=$(curl -sS -o "$body" -w '%{http_code} %{time_total}' --max-time 60 -X POST -H @"$hdr" \
  "https://bedrock-runtime.${AWS_REGION}.amazonaws.com/model/${MODEL}/converse" \
  --data '{"messages":[{"role":"user","content":[{"text":"Reply with the single word OK."}]}],"inferenceConfig":{"maxTokens":16,"temperature":0}}' \
  2>&1) || res="000 0"
code=${res%% *}; secs=${res##* }

case "$code" in
  200) ok "bedrock" "$MODEL in $AWS_REGION answered in ${secs}s${BEDROCK_VALID_UNTIL:+ (the key works until $BEDROCK_VALID_UNTIL)}" ;;
  403) if [ -n "${BEDROCK_VALID_UNTIL:-}" ] && [ "$(date -u +%s)" -ge "$(date -u -d "$BEDROCK_VALID_UNTIL" +%s 2>/dev/null || echo 9999999999)" ]; then
         bad "bedrock" "HTTP 403: the tutorial key's cutoff ($BEDROCK_VALID_UNTIL) has passed"
       fi
       bad "bedrock" "HTTP 403: key revoked, expired, or not allowed $MODEL: $(head -c 160 "$body")" ;;
  429) bad "bedrock" "HTTP 429: throttled; retry in a minute" ;;
  000) bad "bedrock" "no connection to bedrock-runtime.${AWS_REGION}.amazonaws.com (egress/DNS?)" ;;
  *)   bad "bedrock" "HTTP $code: $(head -c 160 "$body")" ;;
esac
