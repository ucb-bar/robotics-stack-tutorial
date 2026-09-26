#!/usr/bin/env bash
# Helpers for scripts/93_bedrock_key.sh. Sourced after common.sh (uses step/info/warn/die).
#
# The key is a Bedrock long-term API key (an IAM service-specific credential) on a dedicated
# IAM user that may only invoke one model in one region. It is a bearer token shared by all
# seats. It never appears on a command line: curl reads it from a header file with mode 600,
# and seats receive it on ssh stdin.

# shellcheck disable=SC1091
. "$IISWC_ROOT/aws/bedrock/bedrock.conf"
BEDROCK_SERVICE=bedrock.amazonaws.com
BEDROCK_STATE_DIR="$IISWC_OUT/bedrock"          # test results; no secrets
_BEDROCK_AWS=(aws --region "$BEDROCK_REGION")

bedrock_need() {
  local c; for c in aws jq curl; do command -v "$c" >/dev/null || die "'$c' is required (see docs/BEDROCK.md §2)"; done
}

bedrock_is_profile() { [[ "$1" =~ ^(us|eu|apac|global|us-gov|jp|au|ca)\. ]]; }
bedrock_base_model() { if bedrock_is_profile "$1"; then echo "${1#*.}"; else echo "$1"; fi; }

# Cutoff string -> UTC ISO 8601 ("" for none). Times without a zone are read in BEDROCK_TZ.
bedrock_to_utc() {
  local v=$1 s
  case "$v" in ""|none|off) echo ""; return 0 ;; esac
  s=$(TZ="$BEDROCK_TZ" date -d "$v" +%s 2>/dev/null) || die "'$v' is not a date (e.g. \"2026-09-27 20:00\", read in $BEDROCK_TZ)"
  date -u -d "@$s" +%Y-%m-%dT%H:%M:%SZ
}
bedrock_until_utc()  { bedrock_to_utc "${BEDROCK_VALID_UNTIL:-}"; }
bedrock_utc_local()  { [ -n "$1" ] && TZ="$BEDROCK_TZ" date -d "$1" '+%a %b %-d %Y %-I:%M %p %Z'; }
bedrock_until_local() { bedrock_utc_local "$(bedrock_until_utc)"; }

# Days for --credential-age-days: explicit BEDROCK_KEY_AGE_DAYS, else ceil(time until cutoff), else 7.
bedrock_age_days() {
  if [ -n "${BEDROCK_KEY_AGE_DAYS:-}" ]; then echo "$BEDROCK_KEY_AGE_DAYS"; return; fi
  local u; u=$(bedrock_until_utc)
  [ -n "$u" ] || { echo 7; return; }
  local s=$(( $(date -d "$u" +%s) - $(date +%s) ))
  [ "$s" -gt 0 ] || die "BEDROCK_VALID_UNTIL ($u) is in the past"
  echo $(( (s + 86399) / 86400 ))
}

# Full policy for the key's user: one model, with the cutoff (if any) on every statement.
bedrock_render_policy() {
  local until; until=${2-$(bedrock_until_utc)}
  _bedrock_render_policy_body "${1:-$BEDROCK_MODEL_ID}" \
    | jq --arg u "$until" 'if $u == "" then . else
        .Statement |= map(.Condition = ((.Condition // {}) + {DateLessThan: {"aws:CurrentTime": $u}})) end'
}

_bedrock_render_policy_body() {
  local model=$1
  if bedrock_is_profile "$model"; then
    local prof
    prof=$("${_BEDROCK_AWS[@]}" bedrock get-inference-profile --inference-profile-identifier "$model" --output json)
    jq -n --argjson p "$prof" '{Version:"2012-10-17",Statement:[
      {Sid:"InvokeViaOneInferenceProfile",Effect:"Allow",
       Action:["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],Resource:[$p.inferenceProfileArn]},
      {Sid:"ProfileDestinationsOnlyViaThatProfile",Effect:"Allow",
       Action:["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],Resource:[$p.models[].modelArn],
       Condition:{StringEquals:{"bedrock:InferenceProfileArn":$p.inferenceProfileArn}}},
      {Sid:"AllowBedrockApiKeyAuth",Effect:"Allow",Action:"bedrock:CallWithBearerToken",Resource:"*"}]}'
  else
    jq -n --arg arn "arn:aws:bedrock:${BEDROCK_REGION}::foundation-model/${model}" '{Version:"2012-10-17",Statement:[
      {Sid:"InvokeOneModelOnly",Effect:"Allow",
       Action:["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],Resource:[$arn]},
      {Sid:"AllowBedrockApiKeyAuth",Effect:"Allow",Action:"bedrock:CallWithBearerToken",Resource:"*"}]}'
  fi
}

bedrock_user_exists() { aws iam get-user --user-name "$BEDROCK_IAM_USER" >/dev/null 2>&1; }

# id <TAB> status <TAB> created <TAB> expires
bedrock_list_keys() {
  aws iam list-service-specific-credentials --user-name "$BEDROCK_IAM_USER" --service-name "$BEDROCK_SERVICE" \
    --output json 2>/dev/null \
  | jq -r '.ServiceSpecificCredentials[]? | [.ServiceSpecificCredentialId,.Status,(.CreateDate//"-"),(.ExpirationDate//"-")] | @tsv'
}

bedrock_load_secret() {
  [ -f "$BEDROCK_SECRET_FILE" ] || die "no key at $BEDROCK_SECRET_FILE; run: scripts/93_bedrock_key.sh create"
  # shellcheck disable=SC1090
  set -a; . "$BEDROCK_SECRET_FILE"; set +a
}

bedrock_converse_url() { echo "https://bedrock-runtime.$1.amazonaws.com/model/$(jq -rn --arg m "$2" '$m|@uri')/converse"; }
bedrock_tiny_body()    { jq -nc '{messages:[{role:"user",content:[{text:"Reply with the single word OK."}]}],inferenceConfig:{maxTokens:16,temperature:0}}'; }

# bedrock_http <method> <url> [json-body] -> "HTTP_CODE SECONDS\n" on stdout; body to $BODY_FILE.
bedrock_http() {
  local method=$1 url=$2 body=${3:-} hdr
  [ -n "${AWS_BEARER_TOKEN_BEDROCK:-}" ] || die "AWS_BEARER_TOKEN_BEDROCK not loaded"
  hdr=$(mktemp); chmod 600 "$hdr"
  printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$AWS_BEARER_TOKEN_BEDROCK" > "$hdr"
  local args=(-sS -o "${BODY_FILE:-/dev/null}" -w '%{http_code} %{time_total}\n' -X "$method" -H @"$hdr" --max-time 180)
  [ -n "$body" ] && args+=(--data "$body")
  curl "${args[@]}" "$url" || echo "000 0"
  rm -f "$hdr"
}
