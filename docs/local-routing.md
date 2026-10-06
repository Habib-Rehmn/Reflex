# Local routing and guardrails

The `bootstrap_rules` classifier recognizes explicit commands and exact FAQs.
It is not Laya. Its confidence of 1 means an exact rule matched, not a calibrated
probability of correctness. Unknown requests have confidence 0 and escalate.

| Capability | Example prompt | Local behavior |
| --- | --- | --- |
| FAQ | `What is Reflex?` | Exact catalog answer, ignoring case and surrounding spaces |
| Arithmetic | `calc: (2 + 3) * 4` or `0.1 + 0.2` | Bounded decimal arithmetic |
| Support | `support: I need a refund` | Suggests billing; creates and sends nothing |
| Lookup | `lookup: service.api` | Reads a public demonstration catalog entry |
| Classification | `classify: This is great` | Returns a keyword sentiment label |

## Arithmetic

Supported operations are `+`, `-`, `*`, `/`, `//`, `%`, unary signs and parentheses.
Evaluation interprets allowed syntax nodes; it never calls `eval`, executes code,
imports modules, invokes functions, or accesses attributes. Inputs are at most 128
characters and 64 syntax nodes. Literals and intermediate results must have
absolute value at most one trillion. Decimal precision is 28 significant digits;
division may round. Floor division and modulo use Python's sign convention.

Powers, scientific notation, variables and functions are unsupported. Invalid
`calc:` requests return `Cannot calculate: ...` locally, without an LLM call.
These are handled commands, so their HTTP status is 200 and route is `fast_path`.
General explanations of mathematics still escalate.

## Support and classification

Support keywords are grouped into billing, technical and account departments.
No match or matches in multiple departments escalate. Suggestions are based on
keywords, not an actual support organization or ticketing integration.

Sentiment uses a small positive/negative word list. Mixed polarity, no matching
words, and selected negations/contractions escalate. The result explicitly says
`Keyword sentiment`. Rules do not reliably understand sarcasm, all negations,
context or languages other than English.

## Lookup

The in-process catalog includes `service.api`, `service.health`, and
`project.reflex`. Unknown keys return a local not-found answer. This catalog has
no customer records, live status or private data. SQLite persistence comes in the
next commit; no external lookup is implied.

## Safety precedence

1. Run local preflight checks before calling the classifier.
2. Matching harmful-instruction phrases receive risk 4 and route `block`.
3. Matching private-key headers, `sk-` token shapes, or assigned password/API-key
   labels receive risk 3 and route `review`.
4. Otherwise obtain the classifier prediction and apply risk thresholds before
   executing any matched handler or calling System 2.

Preflight rules recognize a few patterns (credential theft, phishing creation,
and bomb construction), including those embedded in local command strings.
They do not provide comprehensive moderation. Quoted, negated or educational uses
of matching phrases can be blocked; obfuscation, paraphrases and other languages
can evade detection. Secret detection also has false positives and false negatives.
A non-match is not proof that a prompt is safe or free of sensitive data.

No human review queue exists: `review` holds the request without forwarding it,
storing it, or creating a task for a person. Block and review responses do not echo
the prompt. A future classifier's risk 3–5 decisions also prevent handler/provider
execution. Intent alone cannot force a handler: the original prompt must match
that handler and confidence/complexity requirements must pass.

Laya integration remains pending its repository/documentation URL. Do not describe
the current rules as an implemented Laya model or production safety guarantee.
