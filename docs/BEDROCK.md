# Bedrock API key

ModelBlaster's LLM backend (`BACKEND=llm`) calls Amazon Bedrock with a bearer token. This page
covers how that token is created, tested, copied to the seats, rotated and revoked. It is for
whoever runs the AWS side of the tutorial. Participants do nothing; the key is already on their
seat when they log in.

The behavior and numbers below come from running `scripts/93_bedrock_key.sh` end to end against a
freshly created IAM user in the same account as the seat instances, not from AWS documentation
alone.

## 1. What the key is and what it can access

| | |
|---|---|
| Kind | Bedrock *long-term API key*, an IAM service-specific credential for `bedrock.amazonaws.com` |
| Owner | IAM user `iiswc-2026-bedrock`, created by the script. The key is its only credential: no console password, no access keys, no groups, no managed policies. `create` refuses to continue if any of those appear |
| Permission | One inline policy: `bedrock:InvokeModel` / `InvokeModelWithResponseStream` on one model ARN in one region, plus `bedrock:CallWithBearerToken`. Nothing else in AWS: no EC2, no S3, no other model, no other region, not even listing models |
| Model | `deepseek.v3.2` in `us-west-2` (DeepSeek V3.2, invoked on demand). Change it in `aws/bedrock/bedrock.conf` |
| Lifetime | Hard cutoff, by default 2026-09-27 20:00 Los Angeles time (= 03:00Z Sep 28, PDT), enforced by AWS to the second. It is movable on the live key (§4a). The key also expires on its own a few days later as a backstop, and can be revoked at any moment |
| Where it lives | Admin master copy: `secrets/bedrock.env` (untracked, mode 600). Seats: `~/.config/iiswc/bedrock.env` (mode 600) |

All seats use the same key on purpose. Bedrock checks the bearer token on every request and does
not bind it to a machine or IP, so 60 seats with the same token are 60 separate clients. What they
do share is the account's quota per minute for the model, which §5 measures.

The key belongs to whichever AWS account is logged in when `create` runs. That account needs
access to the model (preflight checks this), and its quotas are the ones that apply. The seats can
be in any account, or in none. They only need HTTPS to `bedrock-runtime.us-west-2.amazonaws.com`.

## 2. Prerequisites (admin machine)

* AWS CLI v2, logged in (`aws login`) as an admin of the account that will own the key.
  The minimum policy is `aws/bedrock/operator-policy.json`. Root works but prints a warning.
* `jq`, `curl`, `ssh`.
* For `distribute`, noninteractive ssh to every seat, for example `--ssh-key` with the tutorial keypair.

Participants' seats need only `bash` and `curl`.

## 3. Runbook

| When | Command | What it proves |
|---|---|---|
| 2 days before | `scripts/93_bedrock_key.sh create` | preflight (model access, quotas) → user → policy → key → waits until the key really answers |
| | `scripts/93_bedrock_key.sh test --load 60` | the allowed model answers; another model, another region and the control plane are all 403; 60 simultaneous calls succeed |
| | `scripts/93_bedrock_key.sh test --realistic 60 5` *(optional, ≈ $1.20)* | 60 users × 5 calls of ModelBlaster's size (≈4.5k tokens in, ≤1k out) |
| | `scripts/93_bedrock_key.sh distribute --seats aws/bedrock/seats.txt --ssh-key <pem>` | the key lands on every seat (mode 600) and answers from that seat |
| image build | `scripts/93_bedrock_key.sh pricing [path/to/pricing.yaml]` | ModelBlaster's cost cap can price the model (§6). Contains no secret, so it may go into the AMI |
| tutorial day | `scripts/01_doctor.sh` or `scripts/94_bedrock_check.sh` on a seat | this seat reaches the model |
| during | `scripts/93_bedrock_key.sh status` | keys and expiry, the policy, and invocations / tokens / throttles for the last N hours |
| any time | `scripts/93_bedrock_key.sh cutoff ["YYYY-MM-DD HH:MM"]` | show or move the hard stop (§4a) |
| after | nothing: the key stops at the cutoff. `scripts/93_bedrock_key.sh revoke` stops it earlier | every key Inactive; seats refused within ~15 s |
| cleanup | `scripts/93_bedrock_key.sh revoke --delete-user` | user, keys and policy gone |

The seat list goes in `aws/bedrock/seats.txt`, which is untracked. `aws/bedrock/seats.example`
shows the format and the `describe-instances` line that generates the list from the
`tutorial=iiswc-2026` tags.

**Never bake the key into an AMI or a container image.** Images get copied and shared, and a key
inside one outlives its revocation plan. `distribute` pushes the key onto running seats. For new
seats, rerun `distribute`; it is idempotent.

### What `distribute` does on each seat

1. Streams `secrets/bedrock.env` over ssh stdin (never argv) into
   `~/.config/iiswc/bedrock.env.tmp`, then runs `chmod 600` and `mv`, so the write is atomic.
2. Adds one guarded line to `~/.bashrc`, once (skip it with `--no-bashrc`). Every login shell then
   exports the variables, including shells that never source this repo.
3. Streams `scripts/94_bedrock_check.sh` over stdin and runs it on the seat, so the seat needs no
   copy of the repo, no AWS CLI and no jq.
4. Prints one ok/FAIL line per seat and exits with a nonzero status if any seat failed. Rerunning is
   safe.

Inside this repo, `env.sh` also loads the file (it documents the resolution order), so scripts
and labs see the key even without `.bashrc`.

## 4. Rotation and revocation

`rotate` issues a new key, runs the key tests, then sets the old key Inactive (`--delete-old` also
deletes it). **Seats that hold the old key stop working at that point, so run `distribute` right
after.** IAM allows 2 keys per user, which is what lets the old and new key coexist during the
handover.

`revoke` deactivates every key. In testing, seats were refused 12 to 15 s later. It can be undone;
the script prints the `update-service-specific-credential --status Active` command to do it.

A seat whose key no longer works prints
`MISS bedrock HTTP 403: key revoked, expired, or not allowed deepseek.v3.2`.

IAM takes 10 to 15 s to honour a new key. `create` waits for that, up to 2 minutes, before it
reports success.

### 4a. Setting and moving the cutoff

The key stops at a time you choose, to the second, so nobody has to stay up to revoke it. The time
is a condition (`DateLessThan aws:CurrentTime`) on every statement of the key's policy, which means
AWS enforces it and the seats are not involved. A change takes effect within seconds and needs no
new key and no new `distribute`.

Before `create`, set it in `aws/bedrock/bedrock.conf`:
```bash
BEDROCK_VALID_UNTIL="${BEDROCK_VALID_UNTIL-2026-09-27 20:00}"   # the date and time the key stops
BEDROCK_TZ="${BEDROCK_TZ:-America/Los_Angeles}"                  # the clock that time is read on
```
Give the time as a wall clock in `BEDROCK_TZ` would show it. The script handles daylight saving:
`2026-09-27 20:00` is 8 pm PDT (03:00Z), and `2026-12-01 20:00` would be 8 pm PST (04:00Z). An
explicit offset or `Z` (`2026-09-28T03:00:00Z`, `2026-09-27 20:00 -0700`) is also accepted and
overrides `BEDROCK_TZ`. An empty value means no cutoff.

After `create`, move it on the live key with `cutoff`:
```bash
scripts/93_bedrock_key.sh cutoff                        # show: config value, what AWS enforces now, key expiry
scripts/93_bedrock_key.sh cutoff "2026-09-28 18:00"     # extend (or shorten) -- seconds to take effect
scripts/93_bedrock_key.sh cutoff none                   # remove the cutoff
```
`cutoff` also rewrites `BEDROCK_VALID_UNTIL` in `aws/bedrock/bedrock.conf`, so a later `create` or
`rotate` does not bring the old date back. Commit that change. To use a different cutoff for one
run without editing the file, export the variable instead:
`BEDROCK_VALID_UNTIL="2026-09-28 18:00" scripts/93_bedrock_key.sh create`.

**A cutoff can shorten a key's life but cannot extend it past the key's own expiry.** IAM sets
that expiry at creation, in whole days (`--credential-age-days`). `create` picks the smallest whole
number of days that covers the cutoff; with the default cutoff, a key made on Sep 23 gets 5 days.
If you later move the cutoff beyond that, `cutoff` warns you. In that case `rotate` (which sizes
the new key to the new cutoff) and `distribute` again. Set `BEDROCK_KEY_AGE_DAYS` to override the
automatic sizing if you want more headroom from the start.

A cutoff in the past is refused. To stop the key now, use `revoke`.

Seats show the cutoff they were given (`works until …`), and once it passes they print
`HTTP 403: the tutorial key's cutoff (…) has passed`. After a `cutoff` change the seats keep
showing the old time until the next `distribute`, but AWS always enforces the live policy.

Tests with throwaway keys:
* Cutoff 3 minutes ahead: the key answered before it and was refused 2 s after it.
* Extended while live: the key kept answering past the original cutoff, with no change on the seat.
* Shortened while live to 1 minute ahead: refused as soon as that minute passed.

## 5. Load with 60 concurrent users

The account quotas for DeepSeek V3.2 in us-west-2 are 10,000 requests/min and 100,000,000
tokens/min. All seats share them and neither is adjustable. `preflight` prints them.

| Test | Result |
|---|---|
| 60 simultaneous tiny calls | 60/60 ok, 0 throttled, p50 0.8 to 1.3 s, p95 ≤ 1.6 s |
| 60 users × 5 sequential calls of ModelBlaster's size (≈4.5k in / ≤1k out tokens) | 300/300 ok, 0 throttled; wall 99 s; p50 per call 12.6 s, p95 23.9 s, max 34.7 s; ≈925k tokens/min = 0.9 % of the quota; cost $1.17 |

ModelBlaster's client retries a 429 only 3 times, over about 7 s, and then fails the call. That
makes throttling the failure to watch for. At under 1 % of the quota, none occurred. The client's
timeout of 600 s per call is far above the measured worst case.

## 6. Cost

Standard pricing for on demand use of DeepSeek V3.2 in us-west-2 is $0.62 per million input tokens
and $1.85 per million output tokens (AWS Price List API, effective 2026-09-01). A call of
ModelBlaster's size costs about $0.004. Check the price again before the tutorial:

```bash
aws pricing get-products --region us-east-1 --service-code AmazonBedrock \
  --filters Type=TERM_MATCH,Field=regionCode,Value=us-west-2 Type=TERM_MATCH,Field=model,Value="DeepSeek v3.2" \
  --output json | jq -r '.PriceList[]|fromjson|[.product.attributes.usagetype,(.terms.OnDemand[].priceDimensions[]|.pricePerUnit.USD)]|@tsv'
```

AWS has no spend limit per key. Spending is bounded by the key's expiry, `revoke`, `status`, and
ModelBlaster's `MODELBLASTER_MAX_USD` (default 2, per process, set in the seat file).
**`MODELBLASTER_MAX_USD` only works if ModelBlaster knows the model's price.** Its
`benchmarks/config/pricing.yaml` ships Claude prices only. For any other model the tracker prices
every call at `None`, and the cap never trips, without any warning.
`scripts/93_bedrock_key.sh pricing` merges `aws/bedrock/pricing-entry.yaml` into that file. The merge is idempotent and leaves
existing entries intact (checked: 26 models before and after, Claude entries unchanged). With the
entry in place, a call with 11 input and 2 output tokens was priced at $0.00001052.

## 7. Using a different model

Edit `aws/bedrock/bedrock.conf` (or export `BEDROCK_MODEL_ID`), then run `create` and
`distribute`. `create` renders the policy again every time.

* IDs of models invoked on demand (`deepseek.v3.2`, `moonshotai.kimi-k2.5`,
  `qwen.qwen3-coder-480b-a35b-v1:0`) get one ARN in one region.
* Inference profile IDs (`us.moonshotai.kimi-k3`, …) get the profile ARN plus the model ARN in
  each destination region. Those model ARNs can be used only through that profile
  (`bedrock:InferenceProfileArn` condition). `preflight` tells you which kind a model needs.
* Add a matching block to `aws/bedrock/pricing-entry.yaml`, keyed by the same string as `MODEL`.
  Without it the cost cap is inactive for that model.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `preflight: account cannot use <model> yet` | An admin enables the model in the Bedrock console → Model access, in that account and region |
| `create` loops on HTTP 403 then fails after 2 min | Policy did not attach, or the model ID and region do not match. `status` shows the policy |
| seat: `HTTP 403: the tutorial key's cutoff (…) has passed` | Working as intended. To give more time: `cutoff "<new time>"` (rotate first if it is past the key's own expiry, §4a) |
| seat: `HTTP 403 … key revoked, expired, or not allowed` | Key rotated, revoked or expired → `status`, then `distribute` the current key |
| seat: `no connection to bedrock-runtime…` | Seat has no HTTPS egress / DNS |
| seat: `HTTP 429` | Throttled. Check `status` for InvocationThrottles; at the quotas in §5 this should not happen |
| ModelBlaster calls `us-east-1` | `AWS_REGION` not exported: the seat file was not loaded (open a new login shell, or `. env.sh`) |
| `MODELBLASTER_MAX_USD` never trips | Run `pricing` against the ModelBlaster checkout the seat actually uses |
| `the user already holds 2 keys` | `rotate` (it clears an Inactive one), or `revoke --delete-inactive` |
