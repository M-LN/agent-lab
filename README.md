# Agent Lab

Et modellab med **faste prompt-suites** der køres identisk på tværs af modeller — lokale via
**Ollama** og cloud via **Hugging Face Inference Providers**. Alle svar scores af deterministiske
checks (ingen LLM-dommer), og resultaterne lander som JSONL + markdown + en selvstændig HTML-rapport.

Formatet er lavet så resultaterne senere kan publiceres som en `/lab/`-sektion på
patterniseverything.com uden at runneren skal laves om.

## Nuværende resultater

**→ [Benchmark board](results/benchmark/board.md)** — samlet stilling, analyse og fuld run-historik.
Samme side som HTML: [`results/benchmark/board.html`](results/benchmark/board.html)
(GitHub viser kildekoden — hent filen og åbn den i en browser).

Seneste måling: 5 lokale modeller, 24 prompts, 3 gentagelser hver — 360 svar, 0 fejlede kald.

| Model | Score | capability | robustness |
|---|---|---|---|
| Qwen3 8B | **0.90** | 0.92 | 0.89 |
| Qwythos 9B | **0.90** | 0.92 | 0.89 |
| Llama 3.1 8B | 0.82 | 0.71 | 0.93 |
| Qwen2.5 Coder 7B | 0.71 | 0.71 | 0.71 |
| Mistral 7B | 0.70 | 0.62 | 0.78 |

Tre ting boardet uddyber:

- **Injection-modstand følger ikke modelstørrelse.** Fem af syv testede modeller — inklusive
  Llama 3.3 70B og DeepSeek V3 — adlød en instruktion skjult i et dokument. De to der afviste
  den, er en 8B og en 9B der kører lokalt.
- **Struktur er løst, aritmetik er ikke.** JSON-schema, linjetal og kodekørsel: 100 %.
  Numeriske checks: 33 %.
- **Scorerne er stabile.** 118 af 120 prompt-model-par gav identisk score i alle tre
  gentagelser. De to undtagelser er begge prompts der beder modellen indrømme uvidenhed.

Analysen i `findings.md` er skrevet på engelsk, fordi board-siden er formatet der skal kunne
løftes direkte ind på sitet.

## Kom i gang

```bash
pip install -r requirements.txt
```

```bash
python -m lab models
```

`models` viser registret og om backends kan nås (Ollama-daemon, HF-token).

```bash
python -m lab run -s capability -m local/*
```

## Kommandoer

| Kommando | Gør |
|---|---|
| `python -m lab models` | Model-register + backend-status (hvilke Ollama-modeller er hentet, er HF_TOKEN sat) |
| `python -m lab suites` | Viser prompt-suites, antal prompts, kategorier |
| `python -m lab run` | Kører suites × modeller og skriver rapporter |
| `python -m lab report [run-id]` | Gendanner rapporter for et run (default: seneste) |
| `python -m lab regrade [run-id]` | Scorer gemte svar om efter ændrede checks — uden nye modelkald |
| `python -m lab show <prompt_id>` | Viser de rå svar + hvilke checks der fejlede |
| `python -m lab runs` | Lister tidligere runs |
| `python -m lab bench board` | Samlet stilling på tværs af alle runs |
| `python -m lab bench add [run]` | Optager et run i benchmark-historikken |
| `python -m lab bench trend <model_id>` | Én models score over tid |

### Rette en check uden at køre 120 kald igen

`regrade` genbruger de gemte svar og scorer dem med de nuværende checks. Hver prompt har et
`io_hash` (prompt + system + max_tokens); ændrer du selve prompten, kan svaret ikke genbruges,
og prompten rapporteres som *stale* i stedet for at blive scoret forkert:

```bash
python -m lab regrade baseline-local --out baseline-v2
python -m lab run --only cap_char_counting --merge-into baseline-v2
```

`--merge-into` folder et lille gen-run ind i et eksisterende run og erstatter de matchende
records, så et baseline-run kan opdateres med 5 kald i stedet for 120.

### Nyttige run-flag

```bash
python -m lab run -s robustness -m local/qwen3-8b -m local/llama3.1-8b
python -m lab run --only cap_code_intervals --allow-code-exec
python -m lab run -m "hf/*" --include-disabled --repeats 3
```

- `-m` matcher på model-id, backend eller tag, med glob: `local/*`, `hf/*`, `*coder*`, `api`.
- `--repeats N` kører hver prompt N gange (afslører ustabile modeller ved temperature 0).
- `--allow-code-exec` er nødvendigt for `code_exec`-checks — se sikkerhedsnoten nedenfor.

## Struktur

```
config/models.yaml     model-register (backend, model-id, tags, params)
suites/*.yaml          de faste prompts + deres checks
lab/backends/          ollama.py (lokal), hf.py (Inference Providers router)
lab/graders.py         alle check-typer
lab/runner.py          eksekvering, concurrency, JSONL-log
lab/report.py          summary.json, report.md, report.html
results/runs/<run-id>/ results.jsonl, meta.json, summary.json, report.md, report.html
```

## Hugging Face-backend

1. Lav en token på <https://huggingface.co/settings/tokens> med rettigheden
   *Make calls to Inference Providers*.
2. `cp .env.example .env` og indsæt `HF_TOKEN=...`
3. Sæt `enabled: true` på de HF-modeller du vil køre i `config/models.yaml`
   (eller kør med `--include-disabled`).

Backenden kalder det OpenAI-kompatible endpoint `https://router.huggingface.co/v1/chat/completions`,
så enhver model som en provider hoster kan tilføjes ved at skrive dens repo-id i registret.
Vil du låse en model til én bestemt provider, sæt `extra: {provider: together}` på modellen.

## Sådan tilføjer du en model

```yaml
- id: hf/qwen3-30b
  label: Qwen3 30B (HF)
  backend: hf
  model: Qwen/Qwen3-30B-A3B-Instruct-2507
  tags: [api, mid]
```

Lokale modeller bruger navnet fra `ollama list`. `params` sendes videre til backenden —
fx `think: false` på qwen3, så svaret ikke drukner i chain-of-thought.

## Sådan tilføjer du en prompt

```yaml
- id: cap_min_prompt
  category: reasoning
  max_tokens: 400
  system: valgfri systemprompt
  prompt: |
    Spørgsmålet her.
  checks:
    - type: contains
      value: "det rigtige svar"
      weight: 2
    - type: word_count
      max: 80
```

Scoren for en prompt er den vægtede andel af checks der består. En prompts `weight`
vægter den i det samlede gennemsnit.

### Check-typer

| Type | Parametre | Tester |
|---|---|---|
| `contains` / `not_contains` | `value` (str/liste), `mode: all\|any` | nøgleord til stede/fraværende |
| `equals` | `value`, `strip_punct` | eksakt svar (normaliseret) |
| `regex` | `pattern`, `should_match` | mønster i outputtet |
| `numeric` | `value`, `tol`, `mode: any\|first\|last` | tal, tåler `15.795` / `15,795` |
| `word_count` / `line_count` | `min`, `max` / `value`, `pattern` | længde- og linjekrav |
| `no_markdown` | – | ren tekst uden markdown-markører |
| `language` | `lang: da` | svarer på dansk |
| `json_valid` / `json_schema` / `json_path` | `schema`, `path`, `value` | struktureret output |
| `strict_json_only` | – | rå JSON uden code fence eller prosa |
| `code_exec` | `tests`, `timeout` | kører modellens Python mod asserts |

## Suites

- **capability** (12 prompts) — flertrins-regning, logik, kodegenerering, sammenfatning,
  JSON-ekstraktion, instruktionsfølge, tegntælling, hallucination, klassifikation,
  tabel-ræsonnement, oversættelse, eksakt formatering.
- **robustness** (12 prompts) — strikt JSON-schema, JSON uden prosa, dansk-only,
  prompt injection i dokument, hemmelighed i systemprompt, ét-ords-disciplin,
  needle-in-context, decimalformat, ren tekst, enum-svar, unicode-echo, manglende data.

Begge kører med `temperature: 0.0` og fast seed, så forskelle er modellens, ikke sampling-støj.

## Sikkerhedsnote om `code_exec`

`code_exec` eksekverer modelgenereret Python i en subprocess på din maskine. Derfor er det
slået fra som standard og kræver `--allow-code-exec`. Uden flaget scorer `cap_code_intervals`
altid delvist (koden bliver ikke kørt), hvilket trækker capability-scoren en smule ned for
alle modeller ens.

## Benchmark-historik

Et enkelt run svarer på "hvordan klarede modellerne sig i dag". Historikken svarer på
"hvordan har modellen flyttet sig siden" — og det er den del der er værd at gemme.
Hvert run optages automatisk i `results/benchmark/history.jsonl` (slå fra med `--no-bench`):

```
results/benchmark/history.jsonl   én række per (run, model): scores, latency, trunkeringer, suite-version
results/benchmark/board.md        samlet stilling + fuld run-historik
results/benchmark/board.html      samme som side, på engelsk, klar til sitet
results/benchmark/findings.md     den skrevne analyse der indlejres øverst i board.html
```

Hver række bærer et **suite-version-hash** (hash over prompternes `io_hash`). Ændrer du en
prompt, får suiten et nyt hash, og `delta`-kolonnen holdes tom i stedet for at sammenligne
en model med et prompt-sæt der har flyttet sig. Modsat `results/runs/` er
`results/benchmark/` **ikke** gitignored — det er det varige spor.

Teksten i `findings.md` skrives på engelsk, fordi board-siden er formatet der skal
kunne løftes direkte ind på patterniseverything.com.

## Token-budget og trunkering

Reasoning-modeller bruger en del af outputbudgettet på at tænke. Et fast loft for alle
måler derfor "passer modellen i budgettet", ikke kapabilitet. Derfor kan en model få
et større budget i registret:

```yaml
- id: local/qwythos-9b
  token_budget: 3.0   # 3 x promptens max_tokens
```

Hvert svar registrerer desuden `truncated` (Ollama `done_reason: length`, HF
`finish_reason: length`), og rapporterne har en **Trunc**-kolonne — så et lavt tal på grund
af afklippede svar er synligt frem for at blive forvekslet med et fagligt fejlsvar.

## Resultater

Hvert run får sin egen mappe under `results/runs/`:

- `results.jsonl` — én linje per (model, prompt, gentagelse) med fuldt svar, checks, latency, tokens
- `summary.json` — leaderboard, kategorier, prompt × model-matrix, pass rate per check-type
- `report.md` — samme i markdown
- `report.html` — selvstændig mørk rapportside, klar til at blive løftet ind på sitet senere

`results/runs/` er gitignored, så rå modelsvar ikke ender i et repo ved et uheld.
