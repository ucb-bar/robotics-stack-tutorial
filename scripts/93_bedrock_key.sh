#!/usr/bin/env bash
# The Bedrock API key that BACKEND=llm (ModelBlaster) uses on every seat: create, test,
# distribute, rotate, revoke. One key, restricted by IAM to one model in one region.
# The runbook is docs/BEDROCK.md; this header is the command reference.
#
#   scripts/93_bedrock_key.sh preflight                 checks only: identity, model access, quotas
#   scripts/93_bedrock_key.sh create [--new]            IAM user + a policy that can only invoke + an expiring key,
#                                                       then proves the key works  -> secrets/bedrock.env
#   scripts/93_bedrock_key.sh test [--load N] [--realistic USERS CALLS]
#                                                       tests with the key alone: allowed model OK, everything
#                                                       else 403, optional concurrency/realistic load
#   scripts/93_bedrock_key.sh distribute --seats FILE [--ssh-key K] [--jobs N] [--no-bashrc] [--dry-run]
#                                                       copy the key to every seat (ssh stdin, mode 600)
#                                                       and run 94_bedrock_check.sh on each seat
#   scripts/93_bedrock_key.sh rotate [--delete-old]     new key -> test -> old key Inactive
#   scripts/93_bedrock_key.sh revoke [--key ID | --delete-inactive | --delete-user]
#                                                       default: deactivate every key now (reversible)
#   scripts/93_bedrock_key.sh status [--hours H]        keys + expiry, policy, CloudWatch usage
#   scripts/93_bedrock_key.sh cutoff ["YYYY-MM-DD HH:MM" | none]
#                                                       show / move / remove the hard cutoff on the live key
#                                                       (local time in BEDROCK_TZ; seats need nothing)
#   scripts/93_bedrock_key.sh pricing [PRICING_YAML]    merge aws/bedrock/pricing-entry.yaml into
#                                                       ModelBlaster's pricing.yaml (makes MODELBLASTER_MAX_USD work)
#
# Needs: aws CLI v2 logged in as an admin of the account that will own the key (`aws login`),
# jq, curl. Minimum permissions: aws/bedrock/operator-policy.json.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
. "$IISWC_ROOT/scripts/lib/bedrock.sh"

cmd=${1:-help}; shift || true

# ---------------------------------------------------------------------------------------------
do_preflight() {
  bedrock_need
  step "Preflight ($BEDROCK_MODEL_ID in $BEDROCK_REGION)"
  local arn; arn=$(aws sts get-caller-identity --query Arn --output text) || die "not logged in; run: aws login"
  info "identity  $arn"
  [[ "$arn" == *":root" ]] && warn "root credentials: works, but an IAM admin with aws/bedrock/operator-policy.json is better"
  local base fm life types av block=0
  base=$(bedrock_base_model "$BEDROCK_MODEL_ID")
  fm=$("${_BEDROCK_AWS[@]}" bedrock get-foundation-model --model-identifier "$base" --output json) \
    || die "model $base does not exist in $BEDROCK_REGION"
  life=$(jq -r .modelDetails.modelLifecycle.status <<<"$fm"); types=$(jq -r '.modelDetails.inferenceTypesSupported|join(",")' <<<"$fm")
  info "model     $(jq -r .modelDetails.modelName <<<"$fm") ($base)  lifecycle=$life  inference=$types"
  [ "$life" = ACTIVE ] || { warn "model is $life"; block=1; }
  if bedrock_is_profile "$BEDROCK_MODEL_ID"; then
    "${_BEDROCK_AWS[@]}" bedrock get-inference-profile --inference-profile-identifier "$BEDROCK_MODEL_ID" >/dev/null \
      || { warn "inference profile $BEDROCK_MODEL_ID not found"; block=1; }
  elif [[ ",$types," != *",ON_DEMAND,"* ]]; then
    warn "$base is not ON_DEMAND here; set BEDROCK_MODEL_ID to its inference profile (e.g. us.$base)"; block=1
  fi
  av=$("${_BEDROCK_AWS[@]}" bedrock get-foundation-model-availability --model-id "$base" --output json 2>/dev/null || echo '{}')
  if [ "$av" != '{}' ]; then
    info "access    $(jq -c '{authorizationStatus,agreement:.agreementAvailability.status,entitlement:.entitlementAvailability,region:.regionAvailability}' <<<"$av")"
    jq -e '.authorizationStatus=="AUTHORIZED" and .entitlementAvailability=="AVAILABLE" and .regionAvailability=="AVAILABLE"' <<<"$av" >/dev/null \
      || { warn "account cannot use $base yet: enable model access in the Bedrock console (Model access) as an admin"; block=1; }
  fi
  info "quotas (shared by every seat):"
  "${_BEDROCK_AWS[@]}" service-quotas list-service-quotas --service-code bedrock --output json 2>/dev/null \
    | jq -r --arg n "$(jq -r .modelDetails.modelName <<<"$fm")" '.Quotas[]
        | select((.QuotaName|ascii_downcase|contains($n|ascii_downcase)) and (.QuotaName|test("On-demand")))
        | "            \(.Value)  \(.QuotaName)"' || warn "could not read quotas"
  [ "$block" = 0 ] || die "preflight: fix the warnings above first"
  info "preflight ok"
}

# ---------------------------------------------------------------------------------------------
do_create() {
  local new=0; [ "${1:-}" = --new ] && new=1
  do_preflight
  step "IAM user $BEDROCK_IAM_USER"
  if bedrock_user_exists; then info "exists"; else
    aws iam create-user --user-name "$BEDROCK_IAM_USER" \
      --tags Key=tutorial,Value=iiswc-2026 Key=purpose,Value=bedrock-api-key >/dev/null
    info "created"
  fi
  # The key must be the user's only credential and the inline policy its only permission.
  [ "$(aws iam list-access-keys --user-name "$BEDROCK_IAM_USER" --query 'length(AccessKeyMetadata)')" = 0 ] || die "$BEDROCK_IAM_USER has access keys; delete them"
  ! aws iam get-login-profile --user-name "$BEDROCK_IAM_USER" >/dev/null 2>&1 || die "$BEDROCK_IAM_USER has a console password; delete it"
  [ "$(aws iam list-attached-user-policies --user-name "$BEDROCK_IAM_USER" --query 'length(AttachedPolicies)')" = 0 ] || die "$BEDROCK_IAM_USER has managed policies attached; detach them"
  [ "$(aws iam list-groups-for-user --user-name "$BEDROCK_IAM_USER" --query 'length(Groups)')" = 0 ] || die "$BEDROCK_IAM_USER is in IAM groups; remove it"
  local extra; extra=$(aws iam list-user-policies --user-name "$BEDROCK_IAM_USER" --output json | jq -r --arg p "$BEDROCK_POLICY_NAME" '[.PolicyNames[]|select(.!=$p)]|join(",")')
  [ -z "$extra" ] || die "$BEDROCK_IAM_USER has other inline policies: $extra"
  info "no console password, no access keys, no groups, no managed policies"

  step "Policy $BEDROCK_POLICY_NAME"
  local until_utc days; until_utc=$(bedrock_until_utc); days=$(bedrock_age_days)
  mkdir -p "$BEDROCK_STATE_DIR"
  local pol; pol=$(bedrock_render_policy "$BEDROCK_MODEL_ID")
  echo "$pol" > "$BEDROCK_STATE_DIR/policy.rendered.json"
  aws iam put-user-policy --user-name "$BEDROCK_IAM_USER" --policy-name "$BEDROCK_POLICY_NAME" --policy-document "$pol"
  info "invoke $BEDROCK_MODEL_ID in $BEDROCK_REGION, nothing else  ($BEDROCK_STATE_DIR/policy.rendered.json)"
  if [ -n "$until_utc" ]; then info "hard cutoff: $until_utc = $(bedrock_until_local) (policy condition aws:CurrentTime)"
  else warn "no BEDROCK_VALID_UNTIL: the key works until its ${days}-day expiry or a revoke"; fi

  step "Key"
  local reuse=0 cur exp
  if [ "$new" = 0 ] && [ -f "$BEDROCK_SECRET_FILE" ]; then
    cur=$(sed -n 's/^BEDROCK_KEY_ID=//p' "$BEDROCK_SECRET_FILE")
    exp=$(bedrock_list_keys | awk -F'\t' -v id="$cur" '$1==id && $2=="Active"{print $4}')
    if [ -n "$cur" ] && [ -n "$exp" ] && { [ "$exp" = - ] || [ "$(date -d "$exp" +%s)" -gt $(( $(date +%s) + 86400 )) ]; }; then
      reuse=1; info "reusing active key $cur (expires $exp); pass --new for a fresh one"
    fi
  fi
  if [ "$reuse" = 0 ]; then
    [ "$(bedrock_list_keys | wc -l)" -lt 2 ] || die "the user already holds 2 keys (the IAM limit): use rotate, or revoke --delete-inactive"
    mkdir -p "$(dirname "$BEDROCK_SECRET_FILE")"; chmod 700 "$(dirname "$BEDROCK_SECRET_FILE")"
    local resp id val acct
    resp=$(aws iam create-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-name "$BEDROCK_SERVICE" \
             --credential-age-days "$days" --output json)
    id=$(jq -r .ServiceSpecificCredential.ServiceSpecificCredentialId <<<"$resp")
    val=$(jq -r '.ServiceSpecificCredential|(.ServiceCredentialSecret//.ServiceApiKeyValue//.ServicePassword//empty)' <<<"$resp")
    exp=$(jq -r '.ServiceSpecificCredential.ExpirationDate//"-"' <<<"$resp"); unset resp
    [ -n "$val" ] || die "key $id created but its value was not in the response; revoke it: $0 revoke --key $id"
    acct=$(aws sts get-caller-identity --query Account --output text)
    ( umask 077; cat > "$BEDROCK_SECRET_FILE" <<EOF
# IISWC 2026 tutorial Bedrock key. SECRET: never commit, never bake into an AMI or image.
# Key $id, IAM user $BEDROCK_IAM_USER, account $acct. Can invoke ONLY $BEDROCK_MODEL_ID in $BEDROCK_REGION.
# Stops working at ${until_utc:-its expiry} (policy cutoff); key itself expires $exp. Created $(date -u +%FT%TZ) by scripts/93_bedrock_key.sh.
AWS_BEARER_TOKEN_BEDROCK=$val
AWS_REGION=$BEDROCK_REGION
MODEL=$BEDROCK_MODEL_ID
MODELBLASTER_MAX_USD=$BEDROCK_MAX_USD
BEDROCK_KEY_ID=$id
BEDROCK_VALID_UNTIL=$until_utc
EOF
    ); chmod 600 "$BEDROCK_SECRET_FILE"; unset val
    info "key $id (${days}-day expiry: $exp) -> $BEDROCK_SECRET_FILE (mode 600)"
  fi

  step "Waiting for IAM to propagate"
  bedrock_load_secret
  local url code i; url=$(bedrock_converse_url "$BEDROCK_REGION" "$BEDROCK_MODEL_ID")
  for i in $(seq 1 24); do
    read -r code _ < <(bedrock_http POST "$url" "$(bedrock_tiny_body)")
    [ "$code" = 200 ] && break
    info "HTTP $code, retrying ($i/24)"; sleep 5
  done
  [ "$code" = 200 ] || die "key still refused after 2 minutes (HTTP $code): $0 test shows why"
  info "the key answers. Next: $0 test --load 60, then $0 distribute --seats <file>"
}

# ---------------------------------------------------------------------------------------------
do_test() {
  local load=0 n=60 real=0 ru=60 rc=5
  while [ $# -gt 0 ]; do case "$1" in
    --load) load=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { n=$2; shift; } ;;
    --realistic) real=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { ru=$2; shift; }; [[ "${2:-}" =~ ^[0-9]+$ ]] && { rc=$2; shift; } ;;
    *) die "test: unknown option $1" ;; esac; shift; done
  bedrock_load_secret
  # Test with the key alone, not with any ambient AWS credentials.
  unset AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  local tmp ts fails=0; tmp=$(mktemp -d); ts=$(date -u +%Y%m%dT%H%M%SZ)
  # RETURN traps are not local to a function in bash; clear it on first use or it fires on every later return.
  trap 'rm -rf "${tmp:-}"; trap - RETURN' RETURN
  local rows=()
  t_ok()  { printf '  \033[1;32mok\033[0m    %-26s %s\n' "$1" "$2"; rows+=("$(jq -nc --arg t "$1" --arg d "$2" '{test:$t,result:"ok",detail:$d}')"); }
  t_bad() { printf '  \033[1;31mFAIL\033[0m  %-26s %s\n' "$1" "$2"; rows+=("$(jq -nc --arg t "$1" --arg d "$2" '{test:$t,result:"FAIL",detail:$d}')"); fails=$((fails+1)); }
  msg()   { jq -r '.message // .Message // empty' "$1" 2>/dev/null | head -c 110; }

  step "Key ${BEDROCK_KEY_ID:-?}: must reach $MODEL in $AWS_REGION and nothing else"
  local c s
  read -r c s < <(BODY_FILE=$tmp/1 bedrock_http POST "$(bedrock_converse_url "$AWS_REGION" "$MODEL")" "$(bedrock_tiny_body)")
  [ "$c" = 200 ] && t_ok "allowed model" "HTTP 200 in ${s}s: '$(jq -r '[.output.message.content[]?.text//empty]|join("")' "$tmp/1")'" \
                 || t_bad "allowed model" "HTTP $c $(msg "$tmp/1")"
  read -r c _ < <(BODY_FILE=$tmp/2 bedrock_http POST "$(bedrock_converse_url "$AWS_REGION" "$BEDROCK_NEGATIVE_MODEL")" "$(bedrock_tiny_body)")
  [ "$c" = 403 ] && t_ok "other model refused" "HTTP 403 ($BEDROCK_NEGATIVE_MODEL)" || t_bad "other model refused" "HTTP $c for $BEDROCK_NEGATIVE_MODEL (want 403)"
  read -r c _ < <(BODY_FILE=$tmp/3 bedrock_http POST "$(bedrock_converse_url "$BEDROCK_NEGATIVE_REGION" "$MODEL")" "$(bedrock_tiny_body)")
  [[ "$c" =~ ^[45] ]] && t_ok "other region refused" "HTTP $c ($BEDROCK_NEGATIVE_REGION)" || t_bad "other region refused" "HTTP $c in $BEDROCK_NEGATIVE_REGION (want 4xx)"
  read -r c _ < <(BODY_FILE=$tmp/4 bedrock_http GET "https://bedrock.$AWS_REGION.amazonaws.com/foundation-models")
  [[ "$c" =~ ^[45] ]] && t_ok "control plane refused" "HTTP $c (ListFoundationModels)" || t_bad "control plane refused" "HTTP $c (want 4xx)"

  if [ "$load" = 1 ]; then
    step "$n simultaneous requests"
    local url body i; url=$(bedrock_converse_url "$AWS_REGION" "$MODEL"); body=$(bedrock_tiny_body)
    for i in $(seq 1 "$n"); do ( bedrock_http POST "$url" "$body" > "$tmp/l.$i" ) & done; wait
    _bedrock_summarise "$tmp" l "$n" "load x$n"
  fi
  if [ "$real" = 1 ]; then
    step "Realistic: $ru users in parallel, $rc sequential ModelBlaster-sized calls each"
    local snip p u url2
    snip='static void matmul_f32(const float *a, const float *b, float *c, int M, int N, int K) {
  for (int i = 0; i < M; i++) for (int j = 0; j < N; j++) { float acc = 0.f;
    for (int k = 0; k < K; k++) acc += a[i*K + k] * b[k*N + j]; c[i*N + j] = acc; } }
'
    { echo "You are optimizing a RISC-V RVV 1.0 kernel for a Zephyr target. Reference code follows."
      for _ in $(seq 1 40); do printf '%s' "$snip"; done
      echo "Rewrite matmul_f32 using RVV intrinsics (riscv_vector.h), fp32, VLEN-agnostic. Return only C code."; } > "$tmp/prompt"
    p=$(jq -nc --rawfile t "$tmp/prompt" '{messages:[{role:"user",content:[{text:$t}]}],inferenceConfig:{maxTokens:1024,temperature:0.2}}')
    url2=$(bedrock_converse_url "$AWS_REGION" "$MODEL")
    info "~4.5k input + <=1k output tokens per call; at DeepSeek V3.2 rates about \$$(awk -v n=$((ru*rc)) 'BEGIN{printf "%.2f", n*(4500*0.62+1024*1.85)/1e6}') worst case"
    local t0; t0=$(date +%s.%N)
    for u in $(seq 1 "$ru"); do ( for i in $(seq 1 "$rc"); do BODY_FILE=$tmp/rb.$u.$i bedrock_http POST "$url2" "$p" > "$tmp/r.$u.$i"; done ) & done; wait
    _bedrock_summarise "$tmp" r $((ru*rc)) "realistic ${ru}x${rc}" "$t0"
  fi

  mkdir -p "$BEDROCK_STATE_DIR"
  printf '%s\n' "${rows[@]}" | jq -s --arg ts "$ts" --arg m "$MODEL" --arg r "$AWS_REGION" --arg k "${BEDROCK_KEY_ID:-}" \
    '{timestamp:$ts,model:$m,region:$r,key_id:$k,tests:.}' > "$BEDROCK_STATE_DIR/test-$ts.json"
  info "results: $BEDROCK_STATE_DIR/test-$ts.json"
  [ "$fails" = 0 ] || die "$fails test(s) failed"
  printf '\n\033[1;32mAll key tests passed.\033[0m\n'
}

# _bedrock_summarise TMP PREFIX EXPECTED LABEL [T0]
_bedrock_summarise() {
  local tmp=$1 pre=$2 want=$3 label=$4 t0=${5:-} all ok thr oth lat tok="" wall=""
  all=$(cat "$tmp"/"$pre".*)
  ok=$(awk '$1==200' <<<"$all" | wc -l); thr=$(awk '$1==429' <<<"$all" | wc -l); oth=$(( $(wc -l <<<"$all") - ok - thr ))
  lat=$(awk '$1==200{print $2}' <<<"$all" | sort -n | awk '{a[NR]=$1} END{if(NR) printf "p50 %.1fs  p95 %.1fs  max %.1fs", a[int((NR+1)/2)], a[int(NR*0.95+0.999)], a[NR]}')
  if [ -n "$t0" ]; then
    wall=$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN{printf "%.0f", b-a}')
    tok=$(cat "$tmp"/rb.* 2>/dev/null | jq -rs --arg w "$wall" '[.[]|.usage?//empty] | (map(.inputTokens)|add//0) as $i | (map(.outputTokens)|add//0) as $o
      | "tokens in \($i) out \($o)  \((($i+$o)/($w|tonumber)*60)|floor) tok/min  ~$\((($i*0.62+$o*1.85)/1e6*1000|round)/1000)"')
    wall="  wall ${wall}s"
  fi
  if [ "$ok" = "$want" ]; then t_ok "$label" "$ok/$want ok, 0 throttled  $lat$wall  $tok"
  else t_bad "$label" "$ok/$want ok, $thr throttled (429), $oth other  $lat$wall  $tok"; fi
}

# ---------------------------------------------------------------------------------------------
do_distribute() {
  local seats="" key="" jobs=10 bashrc=1 dry=0
  while [ $# -gt 0 ]; do case "$1" in
    --seats) seats=$2; shift ;; --ssh-key) key=$2; shift ;; --jobs) jobs=$2; shift ;;
    --no-bashrc) bashrc=0 ;; --dry-run) dry=1 ;; *) die "distribute: unknown option $1" ;; esac; shift; done
  [ -n "$seats" ] || die "distribute needs --seats FILE (one ssh destination per line, e.g. ubuntu@aws-N.iiswc; see aws/bedrock/seats.example)"
  [ -r "$seats" ] || die "cannot read seats file $seats"; need_file "$BEDROCK_SECRET_FILE" "run: $0 create"
  local ssh_cmd=(${BEDROCK_SSH:-ssh} -o BatchMode=yes -o ConnectTimeout=10)
  [ -n "$key" ] && ssh_cmd+=(-i "$key")
  local dests; dests=$(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$seats" | awk 'NF{print $1}')
  step "Distributing key $(sed -n 's/^BEDROCK_KEY_ID=//p' "$BEDROCK_SECRET_FILE") to $(wc -l <<<"$dests") seat(s) -> ~/$BEDROCK_SEAT_ENV_PATH"
  if [ "$dry" = 1 ]; then printf '    would push to: %s\n' $dests; return 0; fi
  local out; out=$(mktemp -d)
  local rel=$BEDROCK_SEAT_ENV_PATH
  # The key arrives on stdin (never argv) and is written atomically with mode 600.
  # With bashrc, one guarded line in ~/.bashrc exports the variables in every shell on the seat.
  local install="set -e; umask 077; f=\"\$HOME/$rel\"; mkdir -p \"\$(dirname \"\$f\")\"; cat > \"\$f.tmp\"; chmod 600 \"\$f.tmp\"; mv \"\$f.tmp\" \"\$f\""
  [ "$bashrc" = 1 ] && install+="; grep -q 'iiswc-bedrock-env' \"\$HOME/.bashrc\" 2>/dev/null || printf '\\n[ -f \"\$HOME/$rel\" ] && set -a && . \"\$HOME/$rel\" && set +a  # iiswc-bedrock-env\\n' >> \"\$HOME/.bashrc\""
  local d running=0
  for d in $dests; do
    (
      if "${ssh_cmd[@]}" "$d" "$install" < "$BEDROCK_SECRET_FILE" > "$out/$d.log" 2>&1 \
         && "${ssh_cmd[@]}" "$d" 'bash -s' < "$IISWC_ROOT/scripts/94_bedrock_check.sh" >> "$out/$d.log" 2>&1; then
        echo ok > "$out/$d.rc"; else echo FAIL > "$out/$d.rc"; fi
    ) &
    running=$((running+1)); if [ "$running" -ge "$jobs" ]; then wait -n; running=$((running-1)); fi
  done; wait
  local nok=0 nbad=0
  for d in $dests; do
    if [ "$(cat "$out/$d.rc")" = ok ]; then nok=$((nok+1)); printf '  \033[1;32mok\033[0m    %-28s %s\n' "$d" "$(grep -o 'answered in [0-9.]*s' "$out/$d.log" | head -1)"
    else nbad=$((nbad+1)); printf '  \033[1;31mFAIL\033[0m  %-28s %s\n' "$d" "$(sed 's/\x1b\[[0-9;]*m//g' "$out/$d.log" | tail -1)"; fi
  done
  rm -rf "$out"
  info "$nok seat(s) ok, $nbad failed"
  [ "$nbad" = 0 ] || die "fix the failed seats and run it again (it is idempotent)"
}

# ---------------------------------------------------------------------------------------------
do_rotate() {
  local del=0; [ "${1:-}" = --delete-old ] && del=1
  bedrock_need
  local old=""; [ -f "$BEDROCK_SECRET_FILE" ] && old=$(sed -n 's/^BEDROCK_KEY_ID=//p' "$BEDROCK_SECRET_FILE")
  if [ "$(bedrock_list_keys | wc -l)" -ge 2 ]; then
    local inact; inact=$(bedrock_list_keys | awk -F'\t' '$2=="Inactive"{print $1; exit}')
    [ -n "$inact" ] || die "2 ACTIVE keys exist; revoke one first: $0 revoke --key <id>"
    aws iam delete-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$inact"
    info "deleted inactive key $inact to make room"
  fi
  [ -f "$BEDROCK_SECRET_FILE" ] && cp -p "$BEDROCK_SECRET_FILE" "$BEDROCK_SECRET_FILE.previous"
  do_create --new
  do_test
  if [ -n "$old" ]; then
    step "Retiring old key $old"
    warn "seats still holding $old stop working now; run distribute with the new key"
    aws iam update-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$old" --status Inactive
    info "$old is Inactive"
    if [ "$del" = 1 ]; then aws iam delete-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$old"; rm -f "$BEDROCK_SECRET_FILE.previous"; info "$old deleted"; fi
  fi
}

do_cutoff() {
  bedrock_need
  local conf="$IISWC_ROOT/aws/bedrock/bedrock.conf" live now_exp
  live=$(aws iam get-user-policy --user-name "$BEDROCK_IAM_USER" --policy-name "$BEDROCK_POLICY_NAME" --query PolicyDocument --output json 2>/dev/null \
         | jq -r '[.Statement[].Condition.DateLessThan["aws:CurrentTime"] // empty] | unique | join(",")') || true
  # Latest expiry among active keys; the cutoff cannot extend a key past its own expiry.
  now_exp=$(bedrock_list_keys | awk -F'\t' '$2=="Active"{print $4}' | sort | tail -1)
  if [ $# -eq 0 ]; then
    step "Cutoff"
    info "config   BEDROCK_VALID_UNTIL=\"$BEDROCK_VALID_UNTIL\" in $BEDROCK_TZ -> ${BEDROCK_VALID_UNTIL:+$(bedrock_until_utc) = $(bedrock_until_local)}"
    info "live     ${live:-none} ${live:+= $(bedrock_utc_local "$live")}   (what AWS enforces now)"
    info "key      active key expires ${now_exp:-(no active key)}"
    return 0
  fi
  local want=$1 utc
  utc=$(bedrock_to_utc "$want")
  if [ -n "$utc" ] && [ "$(date -d "$utc" +%s)" -le "$(date +%s)" ]; then
    die "$utc is not in the future; to stop the key now use: $0 revoke"
  fi
  bedrock_user_exists || die "IAM user $BEDROCK_IAM_USER does not exist; run: $0 create"
  step "Moving the cutoff: ${live:-none} -> ${utc:-none}"
  aws iam put-user-policy --user-name "$BEDROCK_IAM_USER" --policy-name "$BEDROCK_POLICY_NAME" \
    --policy-document "$(bedrock_render_policy "$BEDROCK_MODEL_ID" "$utc")"
  info "live policy updated: ${utc:-no cutoff} ${utc:+= $(bedrock_utc_local "$utc")}. Takes effect in seconds; seats need nothing"
  # Write it back to the tracked config so a later create/rotate does not revert it.
  local val; [ -n "$utc" ] && val=$want || val=""
  sed -i "s|^BEDROCK_VALID_UNTIL=.*|BEDROCK_VALID_UNTIL=\"\${BEDROCK_VALID_UNTIL-$val}\"|" "$conf"
  info "aws/bedrock/bedrock.conf now says BEDROCK_VALID_UNTIL=\"$val\" (commit it)"
  [ -f "$BEDROCK_SECRET_FILE" ] && sed -i "s|^BEDROCK_VALID_UNTIL=.*|BEDROCK_VALID_UNTIL=$utc|" "$BEDROCK_SECRET_FILE"
  if [ -n "$now_exp" ] && { [ -z "$utc" ] || [ "$(date -d "$utc" +%s)" -gt "$(date -d "$now_exp" +%s)" ]; }; then
    warn "the key itself expires at $now_exp, BEFORE this cutoff. To really last that long: $0 rotate && $0 distribute --seats ..."
  fi
}

do_revoke() {
  bedrock_need
  bedrock_user_exists || { info "IAM user $BEDROCK_IAM_USER does not exist: nothing to revoke"; return 0; }
  local id
  case "${1:-all}" in
    all)   step "Deactivating every key of $BEDROCK_IAM_USER"
           for id in $(bedrock_list_keys | awk -F'\t' '$2=="Active"{print $1}'); do
             aws iam update-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$id" --status Inactive
             info "$id Inactive (undo: aws iam update-service-specific-credential --user-name $BEDROCK_IAM_USER --service-specific-credential-id $id --status Active)"
           done ;;
    --key) id=${2:?--key needs an id}
           aws iam update-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$id" --status Inactive; info "$id Inactive" ;;
    --delete-inactive)
           for id in $(bedrock_list_keys | awk -F'\t' '$2=="Inactive"{print $1}'); do
             aws iam delete-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$id"; info "deleted $id"
           done ;;
    --delete-user)
           step "Deleting IAM user $BEDROCK_IAM_USER, its keys and its policy"
           if [ "${BEDROCK_YES:-}" != 1 ]; then read -r -p "    type 'delete' to confirm: " a; [ "$a" = delete ] || die "aborted"; fi
           for id in $(bedrock_list_keys | cut -f1); do
             aws iam delete-service-specific-credential --user-name "$BEDROCK_IAM_USER" --service-specific-credential-id "$id"; info "deleted key $id"
           done
           aws iam delete-user-policy --user-name "$BEDROCK_IAM_USER" --policy-name "$BEDROCK_POLICY_NAME" 2>/dev/null || true
           aws iam delete-user --user-name "$BEDROCK_IAM_USER"
           rm -f "$BEDROCK_SECRET_FILE" "$BEDROCK_SECRET_FILE.previous"; info "gone" ;;
    *) die "revoke: unknown option $1" ;;
  esac
}

do_status() {
  local hours=24; [ "${1:-}" = --hours ] && hours="${2:?status --hours needs a number of hours}"
  bedrock_need
  step "Keys of $BEDROCK_IAM_USER"
  if bedrock_user_exists; then { printf 'ID\tSTATUS\tCREATED\tEXPIRES\n'; bedrock_list_keys; } | column -t -s $'\t' | sed 's/^/    /'
  else info "(user does not exist)"; fi
  [ -f "$BEDROCK_SECRET_FILE" ] && info "local copy holds $(sed -n 's/^BEDROCK_KEY_ID=//p' "$BEDROCK_SECRET_FILE")"
  step "Policy (the cutoff, if any, is the DateLessThan on every statement)"
  aws iam get-user-policy --user-name "$BEDROCK_IAM_USER" --policy-name "$BEDROCK_POLICY_NAME" --query PolicyDocument --output json 2>/dev/null \
    | jq -r '.Statement[] | "    \(.Sid): \(.Action) on \(.Resource)  until \(.Condition.DateLessThan["aws:CurrentTime"] // "-")"' || info "(none)"
  step "Usage of $BEDROCK_MODEL_ID in the last ${hours}h (whole account, CloudWatch)"
  local q; q=$(jq -nc --arg m "$BEDROCK_MODEL_ID" '["Invocations","InputTokenCount","OutputTokenCount","InvocationThrottles","InvocationClientErrors","InvocationServerErrors"]
    | to_entries | map({Id:"m\(.key)",Label:.value,MetricStat:{Metric:{Namespace:"AWS/Bedrock",MetricName:.value,Dimensions:[{Name:"ModelId",Value:$m}]},Period:3600,Stat:"Sum"}})')
  "${_BEDROCK_AWS[@]}" cloudwatch get-metric-data --start-time "$(date -u -d "-$hours hours" +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
    --metric-data-queries "$q" --output json | jq -r '.MetricDataResults[] | "    \(.Label): \((.Values|add)//0)"'
}

do_pricing() {
  local target=${1:-$ZCS/modelblaster/benchmarks/config/pricing.yaml} py
  need_file "$target" "pass the path of ModelBlaster's benchmarks/config/pricing.yaml"
  py=${WEST_PYTHON:-python3}
  "$py" - "$target" "$IISWC_ROOT/aws/bedrock/pricing-entry.yaml" <<'PY'
import sys, yaml
base = yaml.safe_load(open(sys.argv[1])) or {}
extra = yaml.safe_load(open(sys.argv[2])) or {}
base.setdefault("models", {}).update(extra["models"])
yaml.safe_dump(base, open(sys.argv[1], "w"), sort_keys=False)
print("    merged", ", ".join(extra["models"]), "into", sys.argv[1])
PY
}

case "$cmd" in
  preflight)  do_preflight ;;
  create)     do_create "$@" ;;
  test)       bedrock_need; do_test "$@" ;;
  distribute) do_distribute "$@" ;;
  rotate)     do_rotate "$@" ;;
  revoke)     do_revoke "$@" ;;
  status)     do_status "$@" ;;
  cutoff)     do_cutoff "$@" ;;
  pricing)    do_pricing "$@" ;;
  help|-h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command '$cmd' (try: $0 help)" ;;
esac
