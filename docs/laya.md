# Laya System-1 integration

Reflex now has an optional HTTP classifier for
[NandhaKishorM/laya](https://github.com/NandhaKishorM/laya). This project was selected
because its System-1 decision API matches the original outline. If a different
Laya project was intended, keep `SYSTEM1_PROVIDER=rules` until its API is identified.

The adapter is contract-tested with synthetic responses. A real checkpoint has
not been downloaded or evaluated in this workspace. No model latency, accuracy,
or suitability claim follows from these tests.

## Setup

Install the server in a separate environment so its model dependencies do not
become mandatory gateway dependencies:

```bash
uv venv .laya-venv --python 3.12
uv pip install --python .laya-venv/bin/python 'laya[serve]==0.3.28'
LAYA_HOST=127.0.0.1 LAYA_PORT=8001 LAYA_MODELS=multilingual \
  .laya-venv/bin/laya-serve
```

This requires network access for packages and model weights and enough memory for
inference. Refer to the upstream installation guide for device-specific setup.
The adapter targets the documented `/v1/systemone` protocol, verified against the
upstream source on 2026-10-07. Upstream server upgrades require contract rechecking.

In Reflex's `.env`, set:

```dotenv
SYSTEM1_PROVIDER=laya
LAYA_ENDPOINT=http://127.0.0.1:8001/v1/systemone
LAYA_TIMEOUT_SECONDS=30
```

If the server requires bearer authentication, set the same `LAYA_API_KEY` in both
processes. Use an endpoint URL without embedded credentials, query parameters or
fragments. HTTP is permitted only for loopback hosts; remote endpoints require
HTTPS. Redirects are not followed. Restart Reflex after changing configuration.
Leave `SYSTEM2_PROVIDER=disabled` to test local routing without OpenAI; escalations
will return 503. Enable OpenAI separately when ready.

The default Compose deployment still uses rules. Running Laya on the host does
not make it reachable at a container's `127.0.0.1`. Configure a reachable HTTPS
service and explicitly pass System-1 settings in a local Compose override before
using Laya from Docker. This change does not launch/download Laya automatically.

## Decisions and failure behavior

The adapter submits four choice questions: intent, complexity, need for generated
reasoning, and handling risk. It requests the multilingual model with an 8,192-token
budget. Long inputs and model truncation still need real-world validation; an
expanded budget is not a guarantee that every language/input fits.

Every answer must contain the expected labels, valid finite probabilities that
sum approximately to one, and a selected label with maximum probability. Gateway
confidence is the minimum selected-label probability across all four decisions.
Server abstention flags reduce that decision's confidence to zero. These thresholds
are provisional and require held-out calibration, not just development-set tuning.

Simple maps to complexity 1 and complex to 3. Risk allow maps to 0, review to 3,
and block to 4. An allow decision below 0.85 confidence is held for review. Other
low-confidence decisions escalate through the existing router. The original prompt
must still match a registered local handler; Laya cannot invent or execute tools.

Local secret/harmful-pattern preflight runs before Laya. Timeouts, HTTP errors,
and invalid responses return a sanitized 503 and do not call GPT. There is no
silent fallback to the rules classifier. Provider failure metadata has no selected
route if classification never completed.

## Privacy, metrics, and evaluation

When enabled, prompts that pass local preflight are sent to the configured Laya
endpoint even if ultimately handled locally. Configure a server you control and
review its logging/retention. Reflex stores prediction metadata (`laya_http`), not
the prompt or Laya response body. Local rules remain the default.

Existing token/cost fields and provider-attempt counts refer to System 2 only.
Laya compute, server billing, input tokens and network traffic are not included;
zero GPT cost does not mean zero total serving cost. Query latency includes Laya
classification. The dashboard's provider-attempt metric is labeled GPT accordingly.

The offline benchmark always uses local rules. With `--live`, the benchmark uses
`SYSTEM1_PROVIDER` for the gateway arm and direct GPT for the baseline, and records
the configured classifier. Live evaluation incurs GPT charges. Published savings
must be described as GPT-only estimates, not total infrastructure savings.
