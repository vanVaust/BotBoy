# BotBoy — Überarbeitetes Architektur- und Funktionskonzept

> **Dies ist das gültige Architekturdokument des Repositorys.** Es ersetzt die
> zehn Planungsdokumente, die jetzt unter `docs/archiv/` liegen. Offene Vorhaben
> stehen in `docs/backlog.md`.
>
> Das als Soll-Zustand herangezogene Zielkonzept (im Text `claude/ARCHITEKTUR.md`)
> liegt außerhalb dieses Repositorys, im Projektarbeitsbereich. Es beschreibt
> keinen hier eingecheckten Code, sondern die angestrebten Prinzipien; Kapitel 1.2
> sagt, was davon übernommen wurde und was nicht.

**Stand:** 18.10.2026 · **Gegenstand:** `github.com/vanVaust/BotBoy`, Branch `main`, Commit `5eaec09` („fix: close owned SQLite resources and stabilize Windows verification", 17.10.2026 08:28 UTC) · **Zweck:** Aufräumen und Fertigbauen in einem Zug, nach zusammengeführtem Zielkonzept · **Grundlage der Zusammenführung:** Funktionsumfang des Repos plus der strenge Gateway-, Policy-, Freigabe- und Audit-Kern aus `claude/ARCHITEKTUR.md`, die als Soll-Zustand gilt, nicht als Bestandsbeschreibung.

---

## 0 Kurzfassung

- **Der Code ist deutlich besser als sein Ruf und deutlich schlechter dokumentiert.** Gemessen: 291 Python-Module, 47.330 Zeilen, 87 Testdateien, **395 Tests grün in 35 s**, **64 % Anweisungsabdeckung** (15.818 Anweisungen, 5.670 nicht abgedeckt). Die Behauptung des Vorberichts, das System sei nie real geprüft, ist für dieses Repo **widerlegt** [GESICHERT].
- **Das eigentliche Problem ist nicht fehlende Prüfung, sondern falsch verteilte Prüfung.** `botboy/policy.py` — der Entscheidungspunkt, auf dem das ganze Sicherheitsversprechen ruht — hat **0 % Abdeckung** (62 Anweisungen). Ebenso 0 %: `security/sandbox.py`, `security/tenant_sandbox.py`. Das größte Sicherheitsmodul `gateway/queue_security.py` (263 Anweisungen) liegt bei 46 % [GESICHERT].
- **Das Stdlib-Prinzip ist fast schon erfüllt, nur weiß es niemand.** Von 291 Modulen importieren **2** Fremdcode (`fastapi`, über `find_spec` abgesichert); `yaml` wird an 3 Stellen lazy im Funktionskörper geladen. `requirements.txt` listet dagegen `docker`, `sentence-transformers`, `vosk`, `pyaudio`, `uvicorn`, `websockets`, `aiohttp`, `httpx`, `psutil` — im Paket nicht importiert. Die Abhängigkeitsliste beschreibt eine Software, die es nicht gibt [GESICHERT].
- **Zwei Gateways, und der prinzipientreue ist der besser geprüfte.** `simple_*` (12 Module, 1.610 Anweisungen, reines `http.server`) liegt bei **79 %**, `routes_* + server.py` (FastAPI, 1.173 Anweisungen) bei **75 %**. Die Entfernung des FastAPI-Pfads kostet damit weniger als sie bringt [GESICHERT].
- **Vier echte Verstöße gegen das Zielkonzept**, jeder mit klarem Schnitt: **neun** getrennte SQLite-Dateien statt einer (A2); **kein** fortgeschriebenes Hash-Ketten-Audit, HMAC existiert nur in Auth/CSRF/Secrets (A3); `enable_auth` steht per Vorgabe auf **`False`** (A7); **75** `scripts/`-Verzeichnisse in 105 Skills, ausgeführt über `subprocess` — gegen „kein Shell-Werkzeug" (A6). `shell=True` kommt null mal vor, das ist richtig gemacht [GESICHERT].
- **Aufräumen bringt sofort messbaren Gewinn.** `botboy/bundled/` und `botboy/bundled_assets/` sind **byte-identische** Bäume mit je 131 Dateien — ein ganzer Baum ist ersatzlos entfernbar. Dazu 11 Planungsdokumente auf oberster Ebene mit 4.172 Zeilen und überlappenden Namen, während `docs/` nur 2 Dateien enthält, plus `scratch_test.py` im Wurzelverzeichnis, das ein echtes Testmodul dupliziert [GESICHERT].
- **Auch die Prüfkette nach außen ist besser als angenommen.** Es gibt eine Integrationsprüfung über `windows-2022` **und** `ubuntu-24.04` mit Python 3.10 und 3.13. Was fehlt, ist ein Abdeckungstor — deshalb bleibt die inverse Verteilung aus dem dritten Punkt unbemerkt [GESICHERT].
- **Das Repo ist bereits öffentlich und hat keine Lizenz.** Damit sind alle Rechte vorbehalten: niemand darf den Code nutzen oder weitergeben, über Ansehen und Forken innerhalb von GitHub hinaus. Es fehlen außerdem `SECURITY.md` und ein Meldeweg. Im aktuellen Stand fand ich keine Geheimnisse, die Historie ist wegen des flachen Klons aber ungeprüft [GESICHERT]. Der Vorbericht ging noch von einer Veröffentlichung nach einem Tor aus — das ist überholt (Kapitel 9.1).
- **Empfohlene Reihenfolge:** erst die acht Architekturentscheidungen A1–A8 festschreiben, dann der Aufräumschnitt R1–R9 (reversibel, testgedeckt), dann die Fertigstellung F1–F7. Begründung: Jeder Aufräumschnitt verkleinert die Fläche, auf der die Fertigstellung Fehler machen kann. Umgekehrt baut man Funktionen in Strukturen, die man danach wegwirft.
- **Härtester Einzelschnitt:** ein Gateway statt zwei (A1/R4). Er löscht rund 1.173 geprüfte Anweisungen und erzwingt die Portierung von 11 Sicherheitstestmodulen, die heute über `fastapi.testclient` laufen. Das ist der Preis dafür, dass der ausgelieferte Pfad und der geprüfte Pfad derselbe sind.
- **Gesamtvertrauen: hoch für den Ist-Stand, mittel für die Zielarchitektur, niedrig für alle Aufwandszahlen.** Der Ist-Stand ist in dieser Sitzung selbst gemessen. Die Zielarchitektur folgt aus dem Soll-Konzept plus der Recherche vom 17.10.2026. Aufwände sind Schätzungen ohne Kalibrierung an diesem Repo.

---

## 1 Grundlage, Messmethode und Grenzen

### 1.1 Was gemessen wurde und wie

Alle Ist-Zahlen in Kapitel 2 stammen aus eigenen Messungen am geklonten Repo, nicht aus Dokumenten. Verfahren:

| Größe | Verfahren |
|---|---|
| Dateizahl, Zeilen | `find` plus `wc -l` über `*.py`, ohne `.git` |
| Fremdabhängigkeiten | `grep` auf `^\s*(from\|import)\s+<paket>` über alle Module, gegen `requirements.txt` abgeglichen |
| Testergebnis | `python3 -m pytest -q` nach Installation von `pytest`, `fastapi`, `httpx`, `pyyaml` |
| Abdeckung | `python3 -m coverage run --source=botboy -m pytest`, dann `coverage report` |
| Duplikate | `diff -rq` über die beiden `bundled`-Bäume |
| Datenbanken | `grep` auf `sqlite3.connect` und auf `*.db`-Literale, plus Lesen von `botboy/db_mixin.py` |
| Skript-Oberfläche | Zählung der `SKILL.md`-Dateien und der `scripts/`-Verzeichnisse |

Evidenzstufen wie im Vorbericht: **[GESICHERT]** eigene Messung, Primärquelle oder Standard · **[ETABLIERT]** Sekundärquelle oder breit akzeptiertes Fachwissen · **[HERSTELLERANGABE]** · **[HYPOTHESE]** eigene Schlussfolgerung oder Schätzung · **[UNVERIFIZIERT]**.

### 1.2 Was dieses Konzept von `claude/ARCHITEKTUR.md` übernimmt

ARCHITEKTUR.md gilt als **Soll**. Übernommen werden die Prinzipien und die Kernmechanik, nicht die Bestandsangaben:

**Übernommen:** ein Gateway als einziger Ausführungspfad · Policy mit Grundhaltung Ablehnen · argumentabhängiges Risiko · argumentgebundene, einmal verwendbare Freigaben · fortgeschriebenes Hash-Ketten-Audit · das System verleiht sich nie selbst Rechte · fail closed · Standardbibliothek zuerst · kein Shell-Werkzeug · eine Datenbankdatei · Lernschleife nur mit menschlicher Übernahme.

**Nicht übernommen, weil Bestandsbehauptung:** die Releaseliste 0.1.0 bis 0.4.0, die Schema-Version v6, die Modullisten. Das Repo steht bei `0.6.0-dev`, kennt das Wort „BotBoyz" nicht und enthält weder `CHANGELOG.md` noch `ARCHITEKTUR.md` noch ein `claude/`-Verzeichnis [GESICHERT].

**Zurückgestellt, weil Zielbild ohne Codegrundlage:** Hexagon-Raum, Körper und Bühne, Geräte-Brücken. Kein einziger Treffer im Repo für `hexagon`, `bodies`, `home_assistant`, `adb`, `bluetooth`, `avatar` [GESICHERT]. Sie bleiben Funktionsziele (Kapitel 6), nicht Teil des Aufräumens.

### 1.3 Randbedingung: die Umsetzung erfolgt KI-gestützt

Der Entwickler schreibt den Code nicht selbst. Das ist keine Nebenbemerkung, sondern formt das Konzept:

1. **Jede Vorgabe muss eindeutig sein.** Wo zwei Lesarten möglich sind, wählt ein Modell irgendeine. Deshalb sind alle Schnitte in Kapitel 7 als Vorbedingung, Aktion, Nachbedingung formuliert.
2. **Jede Vorgabe braucht eine maschinell prüfbare Abnahme.** Ohne ausführbares Kriterium ist „fertig" eine Behauptung. Deshalb trägt jeder Baustein in Kapitel 5 und jeder Schnitt in Kapitel 7 eine Prüfzeile.
3. **Strukturelle Zwänge sind Prüfungen vorzuziehen.** Ein Modell, das eine Regel umgehen kann, umgeht sie irgendwann. Ein einziger Ausführungspfad ist stärker als jede Konvention.
4. **Das Wachstumsmuster ist bekannt und muss begrenzt werden.** Die 291 Module zeigen das typische Zerfallsbild: mechanische Aufspaltung in `*_support.py`, `*_service.py`, `*_helpers.py`. Deshalb legt A8 eine verbindliche Paketgrenze fest.

### 1.4 Grenzen

1. Die Abdeckungszahl misst Anweisungen, nicht Zweige und nicht Qualität. 64 % heißt nicht, dass 64 % des Verhaltens geprüft ist.
2. Gemessen wurde unter **Linux, Python 3.13**. Das Zielsystem des Entwicklers ist **Windows** (`win32`, x64). Der jüngste Commit betrifft genau Windows-Stabilisierung, also ist Plattformverhalten ein offener Punkt [GESICHERT für die Plattform, [HYPOTHESE] für die Auswirkung].
3. Der Klon ist flach (`--depth 1`). Aussagen zur Entstehungsgeschichte sind nicht belegbar.
4. Alle Aufwandsklassen sind **[HYPOTHESE]**: S bis 2 Personentage, M 3 bis 7, L 8 bis 20, XL über 20, KI-gestützte Umsetzung unterstellt.
5. Rechtliche Hinweise sind Orientierung, keine Rechtsberatung.

---

## 2 Ist-Stand, gemessen

### 2.1 Umfang und Verteilung

| Größe | Wert | Evidenz |
|---|---|---|
| Python-Module | 291 | [GESICHERT] |
| Python-Zeilen | 47.330 | [GESICHERT] |
| Markdown-Dateien | 256 | [GESICHERT] |
| YAML-Dateien | 75 | [GESICHERT] |
| Testdateien | 87 | [GESICHERT] |
| Testergebnis | 395 bestanden, 2 übersprungen, 74 Subtests, 35 s | [GESICHERT] |
| Abdeckung gesamt | 64 % (15.818 Anweisungen, 5.670 nicht abgedeckt) | [GESICHERT] |
| Version | `0.6.0-dev` in `setup.py` und `botboy/__init__.py` | [GESICHERT] |
| Module mit Fremdimport | 2 von 291 | [GESICHERT] |
| Dateien über 800 Zeilen | 5 | [GESICHERT] |
| Dateien unter 60 Zeilen | 66 | [GESICHERT] |

Die Verteilung ist an beiden Enden kaputt: fünf Module über 800 Zeilen (`tasks.py` 2.357, `evals.py` 1.671, `task_merge_service.py` 1.032, `__main__.py` 1.014, `skills/manager.py` 924) und 66 Module unter 60 Zeilen. Das ist das Bild mechanischer Aufspaltung ohne fachlichen Schnitt [HYPOTHESE zur Ursache, [GESICHERT] zur Verteilung].

### 2.2 Paketstruktur

| Verzeichnis | Module | Zeilen |
|---|---|---|
| `botboy/` flach | 55 | 16.950 |
| `botboy/gateway/` | 40 | 8.487 |
| `botboy/skills/` | 4 | 1.940 |
| `botboy/memory/` | 4 | 910 |
| `botboy/security/` | 7 | 837 |
| `botboy/core/` | 6 | 823 |
| `botboy/llm/` | 5 | 718 |
| `botboy/migrations/` | 3 | 273 |
| `botboy/voice/` | 2 | 151 |
| `botboy/bundled/` + `bundled_assets/` | je 131 Dateien, byte-identisch | — |

55 Module mit 16.950 Zeilen in einem flachen Namensraum sind der größte Strukturmangel. Dort liegen fachlich unverbundene Dinge nebeneinander: `tasks.py`, `evals.py`, `archetypes.py`, `consensus.py`, `voice/`, `a2a_pilot.py`, `delegation_advisor.py`, `reflection_memory.py` [GESICHERT].

### 2.3 Der Sechsfach-Schnitt am Beispiel „Aufgaben"

Ein Fachthema verteilt sich auf sechs Module ohne erkennbare Grenze: `tasks.py` (2.357), `task_merge_service.py` (1.032), `task_command_support.py` (634), `task_merge_helpers.py` (361), `task_security.py` (142), `gateway/task_support.py` (433). Dasselbe Muster bei `cli.py` / `cli_dispatch.py` / `cli_support.py`, bei `runtime.py` / `runtime_command_support.py` / `runtime_surface_support.py` und bei `history.py` / `history_support.py` [GESICHERT].

### 2.4 Abhängigkeiten: Liste gegen Wirklichkeit

| Paket in `requirements.txt` | Import im Paket `botboy/` | Folge |
|---|---|---|
| `pyyaml` (als Pflicht geführt) | 3 lazy Importe in Funktionskörpern (`skills/manager.py`, `skills/ldf.py`, `core/config.py`) | weiche Option, nicht Pflicht |
| `fastapi` | 4 Stellen in `routes_ws.py`, `routes_tasks.py`, `routes_core.py`, `errors.py` (dort über `find_spec` abgesichert) | nur der FastAPI-Pfad |
| `uvicorn`, `websockets`, `aiohttp`, `httpx`, `psutil` | kein Import gefunden | Liste ist falsch |
| `docker`, `sentence-transformers`, `vosk`, `pyaudio` | kein Import gefunden | Liste ist falsch |

`sentence-transformers` zieht PyTorch nach, `vosk` und `pyaudio` brauchen Systembibliotheken. Wer der Liste folgt, installiert ein Vielfaches des Nötigen für Funktionen, die der Code nicht aufruft. Das ist zugleich das billigste Aufräumergebnis des ganzen Vorhabens [GESICHERT].

Wichtige Einschränkung zugunsten des Bestands: **`setup.py` ist bereits richtig gestuft** — `install_requires=["pyyaml>=6.0"]`, dazu die Zusatzgruppen `standard`, `full` und `dev`, und `python_requires=">=3.10"`. Falsch ist allein `requirements.txt`, das alles flach als Pflicht darstellt. Der Fehler liegt also in der Darstellung nach außen, nicht im Verpackungsaufbau [GESICHERT].

### 2.5 Zwei Gateways

| Pfad | Module | Anweisungen | Abdeckung | Technik |
|---|---|---|---|---|
| `simple_*` | 12 | 1.610 | **79 %** | `http.server.BaseHTTPRequestHandler`, reine Standardbibliothek |
| `routes_* + server.py` | 7 | 1.173 | 75 % | `fastapi`, `asyncio`, eigener Lebenszyklus |

`routes_core.py` meldet im Gesundheitsendpunkt `"mode": "fastapi"`, es gibt also einen bewussten Zweibetrieb. Entscheidend: **der prinzipientreue Pfad ist der besser geprüfte.** Die Sicherheitstests laufen allerdings überwiegend über `fastapi.testclient` — 11 Testmodule brachen ohne `fastapi` bereits beim Einsammeln ab, darunter `test_final_execution_authorization`, `test_gateway_auth_security`, `test_execution_tenant_approval_integration`, `test_merge_security_boundary`, `test_trace_tenant_security`, `test_scheduler_execution_boundary` [GESICHERT].

Daraus folgt der wichtigste Satz dieses Kapitels: **Der Pfad, der die Prinzipien erfüllt, und der Pfad, auf dem die Sicherheitsgrenzen geprüft werden, sind heute nicht derselbe.**

### 2.6 Sicherheitskern: Abdeckung im Detail

| Modul | Anweisungen | Abdeckung | Bewertung |
|---|---|---|---|
| `gateway/execution_security_gate.py` | 10 | 100 % | gut |
| `security_policy.py` | 15 | 100 % | gut |
| `gateway/status_read_security.py` | 13 | 100 % | gut |
| `security/governance.py` | 73 | 99 % | gut |
| `security/zero_trust.py` | 52 | 98 % | gut |
| `security_authorization.py` | 35 | 97 % | gut |
| `gateway/security.py` | 151 | 94 % | gut |
| `security_context.py` | 60 | 93 % | gut |
| `approval_store.py` | 96 | 92 % | gut |
| `gateway/merge_security.py` | 84 | 88 % | ausreichend |
| `gateway/recovery_security.py` | 116 | 87 % | ausreichend |
| `security/replay_diff.py` | 163 | 85 % | ausreichend |
| `task_security.py` | 81 | 83 % | ausreichend |
| `gateway/worker_read_security.py` | 132 | 80 % | ausreichend |
| `security/replay_harness.py` | 39 | 62 % | **Lücke** |
| `gateway/queue_security.py` | 263 | **46 %** | **größte Lücke absolut** |
| `gateway/task_store_surface_security.py` | 49 | 29 % | **Lücke** |
| `security/sandbox.py` | 54 | **0 %** | **ungeprüft** |
| `security/tenant_sandbox.py` | 26 | **0 %** | **ungeprüft** |
| `policy.py` | 62 | **0 %** | **ungeprüft, obwohl Entscheidungspunkt** |

Der Befund ist nicht „zu wenig getestet", sondern **invers verteilt**: viele kleine Wächtermodule sind nahezu vollständig geprüft, während der eigentliche Entscheidungspunkt und die Isolationsmodule gar nicht geprüft sind. `policy.py` mit 118 Zeilen und 0 % Abdeckung trägt das Versprechen, das die anderen 21 Module durchsetzen [GESICHERT].

Positiv und im Vorbericht nicht erkannt: **`security/replay_harness.py` und `security/replay_diff.py` existieren bereits.** Der im Vorbericht als Maßnahme O-12 empfohlene Replay-Prüfstand ist teilweise schon gebaut, mit 85 % Abdeckung beim Vergleichsteil [GESICHERT].

### 2.7 Ausführungsweg

`command_execution_service.py` (710 Zeilen) ist ein echter Engpass mit brauchbarer Mechanik [GESICHERT]:

- `require_authorized` aus `security_authorization` vor der Ausführung,
- Freigabepflicht aus dem Skill-Vertrag (`contract["needs_approval"]`),
- eine Freigabe zählt nur, wenn `granted` **und** `explicit` **und** `source == "approval_store"` **und** eine nichtleere `approval_id` **und** `approval_scope == "task.execute"`,
- Ablehnungen werden als `authorization_denied` protokolliert,
- der Ergebnis-Cache ist an einen Sicherheitsbereich samt `authorization_version` gebunden, alte Einträge sind also invalidierbar,
- Befehle mit Freigabepflicht werden nicht aus dem Cache beantwortet.

Das ist bereits nahe am Soll. Was fehlt, ist die **Argumentbindung**: geprüft wird gegen `approval_scope == "task.execute"`, nicht gegen die konkreten Argumente des Aufrufs. Eine Freigabe für einen harmlosen Aufruf deckt damit einen anderen Aufruf desselben Bereichs [HYPOTHESE, aus dem Codepfad abgeleitet, nicht durch Angriffstest belegt].

### 2.8 Persistenz

`botboy/db_mixin.py` (`_SQLiteMixin`, 156 Zeilen) ist die **einzige** Stelle mit `sqlite3.connect` (drei Aufrufe): geteilte Verbindung für `:memory:`, sonst eine Verbindung pro Thread im WAL-Modus, Aufräumen über `atexit` und `weakref.WeakSet` [GESICHERT].

Darauf liegen **neun getrennte Datenbankdateien**: `botboy.db`, `tasks.db`, `traces.db`, `history.db`, `scheduler.db`, `principals.db`, `api_keys.db`, `revoked_tokens.db`, `reflection_memory.db` [GESICHERT].

Das ist der klarste Verstoß gegen das Soll („eine SQLite-Datei") und gleichzeitig der am leichtesten behebbare: weil aller Zugriff durch einen Mixin läuft, ist die Zusammenlegung eine Änderung an einer Stelle plus Schemaarbeit, nicht an 291. Fachlich wiegt schwerer, dass es **über die neun Dateien hinweg keine Transaktion gibt**: eine Aufgabe, ihre Freigabe und ihr Protokolleintrag können nicht gemeinsam festgeschrieben werden. Ein Abbruch zwischen zwei Dateien hinterlässt genau den Zustand, den ein Audit nicht haben darf [HYPOTHESE aus der Struktur].

### 2.9 Audit

Die Suche nach `hash_chain`, `prev_hash` und `hmac` trifft `gateway/secrets.py`, `gateway/csrf.py`, `gateway/auth.py` und `security/zero_trust.py` — also HMAC für Authentisierung, CSRF und Geheimnisse. **Ein fortgeschriebenes, manipulationserkennendes Protokoll über Werkzeugaufrufe und Entscheidungen existiert nicht** [GESICHERT]. Es gibt Ereignisprotokollierung (`authorization_denied` wird geschrieben) und `traces.db`, aber keine Verkettung, keine Signatur über den Kettenkopf und keinen externen Anker.

### 2.10 Skills als Ausführungsfläche

| Größe | Wert |
|---|---|
| `SKILL.md` gesamt | 105 |
| davon in `skills/` | 25 |
| davon in `botboy/bundled*` | 70 (in zwei identischen Bäumen) |
| `scripts/`-Verzeichnisse | 75 |
| `subprocess`-Nutzer im Paket | `skills/manager.py`, `skills/runtime.py`, `skills/ldf.py`, `release_acceptance.py`, `cli_support.py`, `validation.py` |
| `shell=True` | **0 Treffer** |

Dass `shell=True` nirgends vorkommt, ist handwerklich richtig und verhindert die klassische Befehlsinjektion. Es bleibt aber dabei: 75 Skriptverzeichnisse sind 75 Einstiegspunkte für Code, der nicht durch den Ausführungsengpass geht, sondern von ihm gestartet wird. `skills/ldf.py` mit 305 Anweisungen und **0 %** Abdeckung und `skills/manager.py` mit 45 % sind dabei die Torwächter [GESICHERT].

Die Recherche vom 17.10.2026 stützt die Schwere: Skills mit Skripten sind nach dem dort ausgewerteten Befund **2,12-mal häufiger verwundbar**, und Agenten erzeugten in **65,5 %** der Fälle unsichere Werkzeuge [ETABLIERT, Sekundärquelle, siehe Vorbericht Kapitel 9].

### 2.11 Vorgabewerte

`botboy/core/config.py:33` setzt `enable_auth: bool = False`. Die Testausgabe enthält entsprechend wiederholt `[SECURITY WARNING] BotBoy auth is DISABLED. All API endpoints are publicly accessible.` [GESICHERT].

Zwei Dinge daran sind getrennt zu bewerten. Dass gewarnt wird, ist gut. Dass die **Testsuite vollständig grün ist, während Authentisierung aus ist**, heißt, dass kein Test die sichere Vorgabe erzwingt. Eine Warnung ist kein fail closed.

### 2.12 Dokumentenlage

| Ort | Dateien | Zeilen |
|---|---|---|
| Wurzelverzeichnis | 11 `*.md` | 4.172 |
| `docs/` | 2 | — |
| `README.md` | 1 | 183 |

Im Wurzelverzeichnis konkurrieren `BOTBOY_V2_V3_MASTER_PROGRAM.md` (854), `BOTBOY_V1_SYSTEM_REFERENCE.md` (788), `BOTBOY_V2_V7_ROADMAP.md` (600), `BOTBOY_V2_V3_EXECUTION_BACKLOG.md` (587), `BOTBOY_V2_V7_STRATEGIC_ROADMAP.md` (553), `BOTBOY_SKILL_PORTFOLIO.md` (338), `BOTBOY_V2_5_TO_V5_EXECUTION_ROADMAP.md` (100), `BOTBOY_TOP12_SKILL_BUILD_PLAN.md` (67), `AUDIT_PLAN.md` (63), `RELEASE_REFACTOR_PLAN.md` (39). Zwei Dokumente tragen „V2_V7" im Namen, zwei weitere „V2_V3". Es gibt **kein** `CHANGELOG.md` und **keine** Architekturdatei im Repo [GESICHERT].

Für einen Außenstehenden ist damit nicht entscheidbar, welcher Plan gilt. Für ein Modell, das an diesem Repo arbeitet, ist es schlimmer: es liest widersprüchliche Vorgaben und wählt eine.

### 2.13 Restmüll

- `scratch_test.py` im Wurzelverzeichnis läuft in der Suite mit und enthält `OrchestratorServiceIntegrationTests::test_bootstrap_service_matches_orchestrator_initialize` — dieselbe Klasse und derselbe Test wie `tests/test_orchestrator_service_integration.py` [GESICHERT].
- `botboy/bundled/` und `botboy/bundled_assets/` sind byte-identisch, je 131 Dateien [GESICHERT].
- Keine Git-Tags, nur `main` [GESICHERT].

### 2.14 Module ohne eigene Prüfung

Abdeckung 0 % trotz Substanz: `skills/ldf.py` (305), `memory/hybrid.py` (237), `release_smoke.py` (93), `voice/listener.py` (88), `consensus.py` (63), `policy.py` (62), `memory/graph.py` (62), `security/sandbox.py` (54), `core/state.py` (51), `llm/context.py` (46), `core/sync.py` (42), `security/tenant_sandbox.py` (26) — zusammen **1.131 Anweisungen ungeprüft** [GESICHERT].

Darunter stehen drei Gruppen mit unterschiedlicher Konsequenz:

1. **Sicherheitsrelevant und zu prüfen:** `policy.py`, `security/sandbox.py`, `security/tenant_sandbox.py`.
2. **Fachlich tragend und zu prüfen oder zu verkleinern:** `skills/ldf.py`, `memory/hybrid.py`, `memory/graph.py`, `core/state.py`, `core/sync.py`.
3. **Kandidaten zum Entfernen:** `consensus.py`, `voice/listener.py`, `llm/context.py`, `release_smoke.py` — unbenutzt, unbelegt und außerhalb des Kerns. Entfernen ist billiger als Prüfen, solange kein Funktionsziel sie verlangt (Entscheidung in R6).
---

## 3 Soll-Ist-Abgleich

Jede Zeile: Vorgabe aus dem Zielkonzept, gemessener Zustand im Repo, Urteil, Auflösung. Urteile: **erfüllt** · **teilweise** · **offen** · **Konflikt** (Code widerspricht der Vorgabe aktiv).

### 3.1 Prinzipien

| Nr | Vorgabe (Soll) | Gemessener Zustand | Urteil | Auflösung |
|---|---|---|---|---|
| P1 | Gateway ist der einzige Ausführungspfad | Zwei HTTP-Pfade (`simple_*` 79 %, FastAPI 75 %); Ausführung selbst läuft über `command_execution_service` mit `require_authorized` | **teilweise** | A1: ein Gateway. A6: Skriptstart als zweiter Pfad entfernen |
| P2 | Policy mit Grundhaltung Ablehnen | `policy.py` 118 Zeilen, **0 % Abdeckung**; Durchsetzung verteilt auf 21 Wächtermodule | **Konflikt** | A4: Policy als einziger Entscheidungspunkt, Ablehnen als Vorgabe, forbid gewinnt |
| P3 | Argumentabhängiges Risiko, fail closed | Risiko kommt aus dem Skill-Vertrag (`needs_approval`), nicht aus den Argumenten | **offen** | A4: `risk_fn(tool, args)`, Ausnahme bedeutet höchste Stufe |
| P4 | Freigaben argumentgebunden und einmalig | Freigabe geprüft gegen `approval_scope == "task.execute"`, `explicit`, `approval_id`, Quelle `approval_store` — aber ohne Argumentbezug | **teilweise** | A5: Bindung an Hash über Werkzeug, kanonische Argumente, Mandant, Lauf; einmalige Verwendung |
| P5 | Fortgeschriebenes Hash-Ketten-Audit | HMAC nur in Auth, CSRF, Secrets; keine Verkettung, kein Kettenkopf, kein Anker | **offen** | A3: Kettentabelle, geschlüsselte Commitments, externer Anker |
| P6 | Das System verleiht sich nie selbst Rechte | `authorization_version` im Cache-Bereich vorhanden; keine Selbstvergabe gefunden | **erfüllt** | halten; A4 ergänzt den Test, der es festschreibt |
| P7 | Fail closed | `enable_auth` steht per Vorgabe auf `False`, Suite grün mit abgeschalteter Authentisierung | **Konflikt** | A7: sichere Vorgaben, Starterweigerung, Vorgabetest |
| P8 | Standardbibliothek zuerst | 2 von 291 Modulen mit Fremdimport; `requirements.txt` listet 13 Pakete, 9 davon ohne Import | **teilweise** | A1 entfernt `fastapi`; A8 stuft Abhängigkeiten; `requirements.txt` auf Wirklichkeit kürzen |
| P9 | Kein Shell-Werkzeug | `shell=True` null Treffer (gut), aber 75 `scripts/`-Verzeichnisse über `subprocess` gestartet | **Konflikt** | A6: drei Stufen, Ziel null Skripte im ausgelieferten Paket |
| P10 | Eine SQLite-Datei | Neun Dateien; Zugriff zentral über `_SQLiteMixin` | **Konflikt** | A2: eine Datei, ein Schema, ein Migrationslauf |
| P11 | Lernschleife nur mit menschlicher Übernahme | `reflection_memory.py` 44 %, `security/replay_harness.py` 62 %, `replay_diff.py` 85 % vorhanden; Übernahmeweg nicht belegt | **teilweise** | F5: Vorschlag, Wirkungsmessung per Replay, Übernahme durch Menschen |
| P12 | Beweisbar statt behauptet | 395 Tests grün, 64 % Abdeckung, aber Entscheidungspunkt und Isolation bei 0 % | **teilweise** | A4, A7, F1: Abdeckungsschwellen pro Zone statt global |

### 3.2 Funktionen

| Nr | Funktion (Soll) | Zustand | Urteil | Folge |
|---|---|---|---|---|
| S1 | Aufgaben, Abhängigkeitsgraph, Warteschlange | `tasks.py` 2.357 Zeilen, `core/dag_engine.py`, `test_dag_engine`, `test_queue_lease_atomicity` | **erfüllt**, aber sechsfach geschnitten | R5 bündelt; `queue_security.py` 46 % auf Schwelle bringen |
| S2 | Arbeiter, Übergabe, Zeitplan | `workers.py`, `worker_handoff_service.py` 767 Zeilen, `scheduler.py` | **erfüllt** | in `domain/` einordnen |
| S3 | Mandantentrennung | `security_context.py` 93 %, `test_mcp_tenant_boundary`, `test_queue_tenant_security`, `security/tenant_sandbox.py` **0 %** | **teilweise** | Sandbox prüfen oder entfernen |
| S4 | MCP-Server und -Client | `botboy_mcp_server.py`, `test_mcp_http_security` | **teilweise** | F4: gegen Revision 2026-07-28 und die Referenz-SDKs prüfen |
| S5 | Gedächtnis | `memory/hybrid.py` 237 Anweisungen **0 %**, `memory/graph.py` **0 %** | **offen in der Prüfung** | F3: bi-temporale Fakten auf FTS5, Goldset, dann messen |
| S6 | Skills | 105 `SKILL.md`, davon 70 in zwei identischen Bäumen; `ldf.py` **0 %** | **Konflikt** | A6, R2, R3 |
| S7 | Bewertung und Abnahme | `evals.py` 1.671 Zeilen, 45 %; `release_acceptance.py` 27 %; `release_smoke.py` **0 %** | **teilweise** | R6 entscheidet Verkleinern gegen Entfernen |
| S8 | Beobachtbarkeit | `tracing.py`, `observability.py`, `monitoring.py`, `metrics.py`, `traces.db` | **erfüllt** | in `ops/`; Trace-Kontext an das Audit binden |
| S9 | Oberfläche: CLI, Web, Kanäle | `cli*.py` (21 % bis 31 %), `web/`, `channels.py`, `__main__.py` 1.014 Zeilen | **teilweise** | R5 bündelt; `__main__` entflechten |
| S10 | Hexagon-Raum | kein Treffer | **offen** | F7, nach dem Kern |
| S11 | Körper und Bühne | kein Treffer | **offen** | zurückgestellt |
| S12 | Geräte-Brücken | kein Treffer | **offen** | zurückgestellt, siehe Kapitel 6.3 |
| S13 | Agent-zu-Agent | `a2a_pilot.py` 273 Zeilen, kein eigenes Testmodul | **teilweise** | zurückgestellt; Konflikt mit P1 (undurchsichtige Fremdausführung) |
| S14 | Konsens mehrerer Modelle | `consensus.py` 63 Anweisungen **0 %** | **offen** | R6: entfernen, bis F7 es verlangt |
| S15 | Sprache | `voice/listener.py` 88 Anweisungen **0 %**, `vosk`/`pyaudio` nicht importiert | **offen** | R6: entfernen |

### 3.3 Das Kernbild in drei Sätzen

Der Funktionsumfang ist weitgehend da und der Ausführungsengpass ist handwerklich brauchbar. Was fehlt, ist der **beweisführende Unterbau**: ein Entscheidungspunkt, der geprüft ist, ein Protokoll, das Manipulation erkennbar macht, eine Datenbank, über die hinweg eine Transaktion reicht, und Vorgaben, die im Zweifel ablehnen. Der Aufräumbedarf ist davon nicht getrennt, sondern dessen Voraussetzung: zwei Gateways, zwei identische Skill-Bäume, neun Datenbanken und elf konkurrierende Pläne machen jeden Beweis doppelt teuer und jede Modellarbeit unzuverlässig.

---

## 4 Architekturentscheidungen

Acht Entscheidungen, jede mit Alternativen, Trade-off-Matrix und maschineller Abnahme. Bewertung in den Matrizen: **+** besser, **o** neutral, **−** schlechter, jeweils gegenüber dem heutigen Zustand.

### A1 Ein Gateway, und zwar der Standardbibliotheks-Pfad

**Entscheidung:** Die `simple_*`-Familie wird der einzige HTTP-Pfad. `botboy/gateway/server.py`, `routes_core.py`, `routes_tasks.py`, `routes_ws.py`, `routes_auth.py`, `routes_fleet.py`, `routes_orgs.py` und der `fastapi`-Zweig in `errors.py` fallen weg. Die 11 Sicherheitstestmodule, die heute über `fastapi.testclient` laufen, werden auf den Standardbibliotheks-Pfad portiert, **bevor** der FastAPI-Pfad gelöscht wird.

**Begründung:** Der prinzipientreue Pfad ist mit 79 % gegen 75 % der besser geprüfte [GESICHERT]. Der Zweibetrieb verdoppelt die Angriffsfläche und erzeugt die gefährlichste Form von Sicherheitsarbeit: Grenzen werden auf dem Pfad geprüft, der nicht ausgeliefert werden soll. Und `fastapi` ist der einzige harte Fremdimport im Paket, also fällt mit ihm P8 von „teilweise" auf „erfüllt".

| Option | Prinzipien | Sicherheit | Aufwand | Latenz | Wartung | Funktionsverlust |
|---|---|---|---|---|---|---|
| **Nur `simple_*` (gewählt)** | + | + | − (Portierung) | o | + | WebSocket |
| Nur FastAPI | − | o | + | + | o | keiner |
| Beide behalten | − | − | + | o | − | keiner |

**Offener Punkt WebSocket:** Bidirektionale Verbindungen existieren nur im FastAPI-Pfad (`routes_ws.py`, 72 Anweisungen, 82 %). Vorschlag: Ersatz durch **Server-Sent Events** über `http.server`. SSE ist einseitig, braucht keinen Handshake nach RFC 6455 und keine Rahmenlogik, und für die Anzeige von Laufzuständen ist die Rückrichtung nicht nötig — Befehle gehen ohnehin als normale Anfragen durch den Engpass. Vor dem Schnitt ist zu prüfen, ob die Weboberfläche die Rückrichtung wirklich nicht nutzt [HYPOTHESE].

**Abnahme:** `grep -rE '^\s*(from|import)\s+fastapi' botboy/` liefert null Treffer · die Suite läuft ohne installiertes `fastapi` vollständig grün · die Abdeckung des verbleibenden Gateway-Pfads liegt nicht unter 79 % · jede zuvor über `testclient` geprüfte Grenze hat ein äquivalentes Testmodul auf dem Standardbibliotheks-Pfad.

### A2 Eine Datei, ein Schema, ein Migrationslauf

**Entscheidung:** Die neun Datenbanken werden zu einer Datei zusammengelegt. `_SQLiteMixin` erhält einen gemeinsamen Pfad; die Tabellen werden nach Fachbereich benannt. `botboy/migrations/runner.py` wird der einzige Weg, der Schemata anlegt oder ändert, mit linearer Versionsnummer und Vorwärtsmigration.

**Begründung:** Erstens Vorgabe P10. Zweitens und wichtiger: Über getrennte Dateien hinweg gibt es keine Transaktion. Aufgabe, Freigabe und Protokolleintrag können heute nicht gemeinsam festgeschrieben werden, ein Abbruch dazwischen hinterlässt genau den Zustand, den ein Audit nicht haben darf. Drittens ist der Schnitt billig, weil aller Zugriff durch **einen** Mixin mit drei `connect`-Aufrufen läuft [GESICHERT].

| Option | Transaktion über Bereiche | Schreibdurchsatz | Sicherung | Aufwand | Audit-Integrität |
|---|---|---|---|---|---|
| **Eine Datei (gewählt)** | + | − (ein Schreiber) | + | − (Migration) | + |
| Neun Dateien | − | o | − | + | − |
| Eine Datei plus eigene Audit-Datei | o | o | o | o | + |

**Zum Durchsatz:** SQLite lässt im WAL-Modus beliebig viele Leser neben **einem** Schreiber zu [ETABLIERT]. Für ein lokales Einzelsystem ist das unkritisch, solange Transaktionen kurz bleiben. Pflicht sind deshalb: `journal_mode=WAL`, ein gesetztes `busy_timeout`, keine offene Transaktion über einen Modellaufruf hinweg, und lange Vorgänge wie Bewertungsläufe schreiben in Stapeln statt in einer Transaktion. Die dritte Option — Audit in eigener Datei — wurde verworfen, weil sie genau die Atomarität zwischen Entscheidung und Protokoll opfert, um die es geht.

**Abnahme:** die Anwendung öffnet genau eine Datei, nachgewiesen über `lsof` oder das Zählen angelegter Dateien im Testlauf · ein Test schreibt Aufgabe, Freigabe und Protokolleintrag in einer Transaktion und weist nach, dass ein erzwungener Abbruch alle drei zurückrollt · der Migrationslauf führt eine Alt-Datenbank jeder der neun Formen verlustfrei über, geprüft mit Zeilenzahlen vor und nach dem Lauf · die Audit-Kette bleibt über die Migration hinweg gültig.

### A3 Audit als fortgeschriebene Kette mit geschlüsselten Commitments

**Entscheidung:** Zwei Tabellen statt einer.

- `audit_chain(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc, tenant, actor, event_type, payload_commit BLOB, prev_hash BLOB, entry_hash BLOB)` mit
  $$\text{entry\_hash} = \mathrm{HMAC}_{k_{\text{tenant}}}\big(\text{prev\_hash} \,\|\, \text{seq} \,\|\, \text{ts\_utc} \,\|\, \text{tenant} \,\|\, \text{actor} \,\|\, \text{event\_type} \,\|\, \text{payload\_commit}\big)$$
- `audit_payload(seq, salt BLOB, payload_json)` mit $\text{payload\_commit} = \mathrm{SHA\text{-}256}(\text{salt} \,\|\, \text{payload\_json})$.

Nur-Anfügen wird durch Trigger erzwungen, die `UPDATE` und `DELETE` auf `audit_chain` ablehnen. Der Kettenkopf wird in einstellbarem Abstand extern verankert: RFC-3161-Zeitstempel und ein signierter Git-Commit.

**Begründung für HMAC statt nacktem SHA-256:** Audit-Felder haben wenig Entropie. Die Prüfung vom 17.10.2026 hat an einem Beispiel gemessen, dass sich über eine Million Hashes pro Sekunde auf einem einzelnen Python-Kern erzeugen lassen; ein nackter Hash über Bezeichner ist damit per Durchprobieren umkehrbar [GESICHERT]. Ein geschlüsseltes Commitment verhindert das.

**Begründung für die Trennung von Kette und Nutzdaten:** Eine Löschpflicht und eine unveränderliche Kette widersprechen sich nur, solange die Nutzdaten *in* der Kette stehen. Steht in der Kette ein Commitment und liegen Nutzdaten samt Salz in einer löschbaren Tabelle, bleibt die Kette nach dem Löschen rechnerisch gültig, und der gelöschte Eintrag ist als gelöscht erkennbar, nicht als verschwunden. Zu beachten: FTS5-Schattentabellen behalten nach den SQLite-Unterlagen auch bei `secure_delete` Spuren, der Lernindex ist daher neu aufzubauen statt punktuell zu löschen [ETABLIERT].

| Option | Manipulation erkennbar | Löschbarkeit | Fremdabhängigkeit | Aufwand | Externer Beweis |
|---|---|---|---|---|---|
| **Kette mit HMAC plus Nutzdaten-Trennung (gewählt)** | + | + | keine | M | + (Anker) |
| Nackte SHA-256-Kette | o (umkehrbar bei wenig Entropie) | − | keine | S | o |
| Transparenzlog eines Dienstes | + | − (öffentlich) | hoch | L | + |
| Hyperledger Fabric | + | − | sehr hoch | XL | + |

Die beiden unteren Optionen fallen weg: ein öffentliches Artefakt-Transparenzlog ist für private Betriebsprotokolle das falsche Werkzeug, und ein Konsortial-Ledger verlangt Containerbetrieb für ein Einzelsystem. Der Anker über Zeitstempel und signierten Commit liefert den gleichen Nutzen — Nachweis, dass die Kette zu einem Zeitpunkt so aussah — ohne Betriebsapparat. Einschränkung: das Committer-Datum eines Git-Commits ist frei setzbar, der Zeitbeweis kommt daher aus dem RFC-3161-Zeitstempel, nicht aus dem Commit [GESICHERT]. Die Ankerfrequenz ist gleich dem Manipulationsfenster.

**Abnahme:** ein Test verändert ein Feld in `audit_chain` und die Kettenprüfung schlägt an der richtigen Stelle fehl · `UPDATE` und `DELETE` auf `audit_chain` werden vom Trigger abgelehnt · nach Löschen eines Nutzdatensatzes bleibt die Kettenprüfung grün und der Eintrag gilt als redigiert · der Anker lässt sich offline gegen den Kettenkopf verifizieren · jeder abgelehnte Aufruf erzeugt genau einen Eintrag, nachgewiesen durch Vergleich der Ablehnungszahl mit der Zahl neuer Einträge.

### A4 Policy als einziger Entscheidungspunkt, Ablehnen als Vorgabe, Verbot gewinnt

**Entscheidung:** `policy.py` wird von 118 Zeilen Nebenrolle zum einzigen Ort, an dem „erlaubt" entsteht. Fünf Eigenschaften:

1. **Grundhaltung Ablehnen.** Keine Regel, keine Erlaubnis.
2. **Argumentabhängiges Risiko.** `risk_fn(tool, args) -> {lesen, schreiben, unumkehrbar, ausgehend}`. Eine Ausnahme in `risk_fn` bedeutet die höchste Stufe, nicht die niedrigste.
3. **Verbot gewinnt, unabhängig von der Reihenfolge.** Eine Verbotsregel schlägt jede Erlaubnisregel.
4. **Effektklassen je Werkzeug**, deklariert, nicht erraten.
5. **Monotone Laufmarkierungen.** Ein Lauf merkt sich unwiderruflich, ob er fremde Inhalte verarbeitet hat, ob er Zugriff auf private Daten hält und ob er nach außen senden kann. Sind alle drei gesetzt, wird abgelehnt.

**Begründung für „Verbot gewinnt":** Die Lernschleife stellt neue Regeln voran. Bei Erstes-Treffer-gewinnt kann eine vorangestellte Erlaubnis eine bestehende Verbotsregel aushebeln — ein Rechteausbau durch Lernen, also genau der Verstoß gegen P6, den das System ausschließen soll [HYPOTHESE, aus der Regelverarbeitung abgeleitet]. Mit „Verbot gewinnt" ist der Angriff strukturell unmöglich statt nur unwahrscheinlich.

**Begründung für monotone Laufmarkierungen statt Argumentverfolgung:** Eine Herkunftsverfolgung auf Argumentebene über Wertvergleich ist bei freiem Werkzeugaufruf nicht dicht, weil das Modell Inhalte umformulieren kann und die Spur dabei verliert [ETABLIERT, Recherchestand 17.10.2026]. Laufweite Markierungen sind gröber, aber nicht umgehbar: sie werden nur gesetzt, nie gelöscht.

**Zur Ehrlichkeit bei Beweisversprechen:** Ein SMT-Löser beweist nichts über Modellausgaben, sondern nur relativ zur Formalisierung. Die Aussage „mathematisch beweisbar keine Halluzinationen" aus dem Forschungsbericht ist ein Überversprechen [GESICHERT als Überversprechen]. Nutzen hat die Analyse der **Regeln** — Erreichbarkeit, Überschneidung, Äquivalenz nach Änderungen. Das ist mit Standardbibliothek über Glob-Automaten und Aufzählung machbar; ein Löser bleibt höchstens Prüforakel in der Integration, nie Laufzeitbestandteil.

| Option | Umgehbarkeit | Prüfbarkeit | Fremdabhängigkeit | Aufwand | Falschablehnungen |
|---|---|---|---|---|---|
| **Policy-Kern mit Verbotsvorrang und Laufmarkierungen (gewählt)** | + | + | keine | M | − (mehr) |
| Erstes-Treffer-gewinnt wie heute | − | o | keine | — | + |
| Externe Regel-Engine (OPA/Cedar) | + | + | hoch, zweiter Prozess | L | o |
| Argumentgenaue Herkunftsverfolgung | o (nicht dicht) | − | keine | L | − |

Mehr Falschablehnungen sind der bewusst gezahlte Preis. Sie sind sichtbar und durch eine menschliche Freigabe behebbar; der umgekehrte Fehler ist unsichtbar.

**Abnahme:** Abdeckung von `policy.py` mindestens **95 %** mit Zweigabdeckung · ein Eigenschaftstest zeigt, dass **jede** Permutation der Regelreihenfolge dieselbe Entscheidung liefert · Vollaufzählung aller Effektsequenzen bis Länge 6 gegen einen absichtlich bösartigen Agenten-Doppelgänger ohne Treffer · ein Test weist nach, dass eine Ausnahme in `risk_fn` zur Ablehnung führt · ein Test weist nach, dass eine durch die Lernschleife vorangestellte Erlaubnisregel ein bestehendes Verbot **nicht** aushebelt.

### A5 Freigaben argumentgebunden, einmalig, befristet

**Entscheidung:** Eine Freigabe bindet an
$$\text{approval\_key} = \mathrm{HMAC}_{k}\big(\text{tool} \,\|\, \mathrm{canon}(\text{args}) \,\|\, \text{tenant} \,\|\, \text{run\_id}\big)$$
mit kanonischer Argumentform (sortierte Schlüssel, normalisierte Zahlen und Pfade). Jede Freigabe hat eine Verfallszeit, einen Verbrauchszähler mit Höchstwert 1 und einen Widerrufsvermerk. Der Verbrauch geschieht in derselben Transaktion wie der Protokolleintrag und die Ausführungsfreigabe.

**Begründung:** Heute prüft `command_execution_service` auf `approval_scope == "task.execute"` samt `explicit`, `approval_id` und Quelle — gut, aber bereichsweit. Eine Freigabe für einen harmlosen Aufruf deckt damit einen anderen Aufruf desselben Bereichs [HYPOTHESE aus dem Codepfad]. Mit Argumentbindung ist die Freigabe das, was ein Mensch gesehen hat, und nichts darüber hinaus.

**Abnahme:** ein Test erhält eine Freigabe für Aufruf X und wird bei Aufruf Y desselben Bereichs abgelehnt · zweite Verwendung derselben Freigabe wird abgelehnt · abgelaufene und widerrufene Freigaben werden abgelehnt · gleichwertige Argumente in anderer Schreibweise ergeben denselben Schlüssel, nachgewiesen über Normalisierungs-Testvektoren · ein Abbruch zwischen Verbrauch und Ausführung lässt keine verbrauchte Freigabe ohne Protokolleintrag zurück.

### A6 Skills ohne Skriptausführung, in drei Stufen

**Entscheidung:** Drei Stufen mit dem Ziel **null Skripte im ausgelieferten Paket**.

| Stufe | Inhalt | Rechte | Weg |
|---|---|---|---|
| 1 | Nur `SKILL.md`: Anleitung, kein Code | frei nutzbar | Zielform |
| 2 | Deklaratives Ablaufmakro: Folge von Gateway-Aufrufen mit Parameterfeldern, **Daten statt Code** | Risiko ist das Maximum der Einzelschritte; jeder Schritt geht durch den Engpass | Zielform für heutige Skripte |
| 3 | Bestandsskript | nur mit Erlaubnisliste samt festgeschriebenem Inhalts-Hash, erzwungener Freigabe und höchster Risikostufe | Auslaufpfad |

**Begründung:** 75 Skriptverzeichnisse sind 75 Einstiegspunkte, die der Engpass startet, statt sie zu durchlaufen. Die Torwächter sind ausgerechnet ungeprüft: `skills/ldf.py` 305 Anweisungen bei **0 %**, `skills/manager.py` bei 45 % [GESICHERT]. Der Forschungsstand stützt die Schwere: Skills mit Skripten sind 2,12-mal häufiger verwundbar, und selbst erzeugte Skills verbessern die Ergebnisse nicht, während kuratierte es tun [ETABLIERT]. Ein Makro aus Daten lässt sich prüfen, versionieren, zurückrollen und im Replay wiederholen; ein Skript nicht.

**Zur Begrenzung des Versprechens:** Die Behauptung des Forschungsberichts, kompilierte Makros liefen mit „0 Token, 0,4 ms, 100 % Determinismus", hält nicht. Auswahl und Parameterbefüllung kosten Tokens oder sind fehleranfällig, und die Laufzeit bestimmen die Werkzeuge, nicht die Ablaufsteuerung [GESICHERT als Überversprechen]. Deterministisch ist allein der Kontrollfluss — was genügt, denn darum geht es.

| Option | Angriffsfläche | Prüfbarkeit | Funktionsverlust | Aufwand |
|---|---|---|---|---|
| **Drei Stufen, Ziel null Skripte (gewählt)** | + | + | o (Umbau nötig) | L |
| Skripte in Sandboxen weiterlaufen lassen | o | o | keiner | L (Isolation) |
| Alles so lassen | − | − | keiner | — |

**Abnahme:** das ausgelieferte Paket enthält kein `scripts/`-Verzeichnis, geprüft im Bauartefakt · `skills/ldf.py` und `skills/manager.py` erreichen mindestens 90 % · ein Test weist nach, dass ein nicht gelistetes Skript und ein gelistetes mit abweichendem Hash abgelehnt werden · ein Test weist nach, dass ein Makro-Schritt ohne Freigabe nicht ausgeführt wird und dass das Makro-Risiko das Maximum seiner Schritte ist · ein Import-Prüfer lehnt eingehende Skills mit Skripten ab.

### A7 Sichere Vorgaben, Starterweigerung, Vorgabetest

**Entscheidung:** `enable_auth` wird `True`. Die Bindung erfolgt per Vorgabe an die lokale Schnittstelle. Der Start **verweigert** sich, wenn Authentisierung aus ist und die Bindung nicht lokal ist. Abschalten erfordert eine ausdrückliche Angabe, die protokolliert wird.

**Begründung:** Die Warnung ist vorhanden und richtig formuliert, aber eine Warnung ist kein fail closed. Entscheidend ist der zweite Befund: die Suite ist vollständig grün, **während** Authentisierung aus ist — kein Test erzwingt die sichere Vorgabe [GESICHERT]. Vorgabewerte sind in der Praxis die Konfiguration, die zählt.

**Abnahme:** ein Test liest die Vorgaben ohne Konfigurationsdatei und ohne Umgebungsvariablen und erwartet Authentisierung an und lokale Bindung · ein Test startet mit abgeschalteter Authentisierung und nichtlokaler Bindung und erwartet Startabbruch · ein Test weist nach, dass das Abschalten einen Audit-Eintrag erzeugt · die Suite läuft ohne Sicherheitswarnung durch, Warnung im Protokoll gilt als Testfehler.

### A8 Paketgrenzen, maschinell erzwungen

**Entscheidung:** Acht Pakete mit gerichteten Importregeln, geprüft durch einen Test.

| Paket | Inhalt | darf importieren |
|---|---|---|
| `kernel/` | Gateway, Policy, Freigaben, Audit, Identität, Geheimnisse, Raten | nur `store/` |
| `store/` | Schema, Migrationen, Datenbankzugriff | nichts aus dem Paket |
| `domain/` | Aufgaben, Graph, Warteschlange, Arbeiter, Übergabe, Zeitplan | `kernel/`, `store/` |
| `knowledge/` | Gedächtnis, Reflexion, Verlauf | `kernel/`, `store/` |
| `skills/` | Verzeichnis, Verträge, Makros | `kernel/`, `store/` |
| `llm/` | Anbindungen, Auswahl, Budgets | `kernel/` |
| `ops/` | Bewertung, Replay, Governance, Überwachung, Kennzahlen | alle außer `surface/` |
| `surface/` | CLI, HTTP-Handler, Anzeige, Kanäle | alle |

Verbindliche Zusatzregeln: kein Modul über 600 Zeilen; die Endungen `_support`, `_helpers` und `_service` sind als Dateinamen unzulässig, weil sie fachlichen Schnitt durch mechanische Aufspaltung ersetzen; ein neues Modul braucht eine Zeile im Paket-Verzeichnisdokument.

**Begründung:** Die 291 Module sind kein Zufall, sondern das Ergebnis fehlender Grenzen bei maschinengestütztem Wachstum. Eine Konvention, die kein Test prüft, hält bei dieser Arbeitsweise nicht. Die Regel „`kernel/` importiert nur `store/`" ist zugleich die strukturelle Fassung von P1: wer am Engpass vorbei ausführen will, müsste die Importregel brechen, und das fällt im Test auf.

| Option | Schutz vor Zerfall | Reibung | Aufwand | Prüfbar |
|---|---|---|---|---|
| **Pakete plus Importtest (gewählt)** | + | o | M (Umzug) | + |
| Nur Stilrichtlinie | − | + | S | − |
| Alles flach lassen | − | + | — | − |

**Abnahme:** ein Test liest alle Importe über den Syntaxbaum und schlägt bei jeder Verletzung der Richtungsregeln fehl · ein Test schlägt bei Modulen über 600 Zeilen fehl · ein Test schlägt bei unzulässigen Dateinamensendungen fehl · `kernel/` hat keinen Import aus `domain/`, `knowledge/`, `skills/`, `llm/`, `ops/` oder `surface/`.
---

## 5 Zielarchitektur

### 5.1 Schichten und der einzige Weg

```
surface/     CLI · HTTP-Handler · Anzeige · Kanäle · Benachrichtigungen
    │  Anfrage {tenant, actor, tool, args, run_id}
    ▼
kernel/      Gateway → Identität → Raten → POLICY → Freigabe → AUDIT → Ausführung
    │                                   (einzige Stelle, an der "erlaubt" entsteht)
    ├──────────────┬──────────────┬──────────────┐
    ▼              ▼              ▼              ▼
domain/        knowledge/      skills/         llm/
Aufgaben       Gedächtnis      Verzeichnis     Anbindung
Graph          Reflexion       Verträge        Auswahl
Warteschlange  Verlauf         Makros          Budgets
Arbeiter
    │              │              │              │
    └──────────────┴──────┬───────┴──────────────┘
                          ▼
                      store/   eine SQLite-Datei · ein Schema · ein Migrationslauf

ops/   Bewertung · Replay · Governance · Überwachung · Kennzahlen  (liest quer, schreibt nur eigene Tabellen)
```

### 5.2 Ausführungsfluss im Detail

Acht Schritte. Die Schritte 3 bis 5 und 7 sind die **einzigen** Stellen, an denen Entscheidungen entstehen oder festgehalten werden.

1. **Anfrage bilden.** `surface/` erzeugt `{tenant, actor, tool, args, run_id}`. Kein Modul aus `surface/` ruft je einen Werkzeugadapter direkt.
2. **Eintritt.** `kernel/gateway`: Authentisierung, Ratenbegrenzung, Auflösung des Prinzipals, Lauf anlegen oder anhängen. Bei abgeschalteter Authentisierung und nichtlokaler Bindung: Abbruch (A7).
3. **Entscheiden.** `kernel/policy`: Effektklasse aus `tool_registry`, dann $\text{risk} = \texttt{risk\_fn}(\text{tool}, \text{args})$ — Ausnahme bedeutet höchste Stufe. Prüfung der Laufmarkierungen; sind fremde Inhalte verarbeitet **und** private Daten erreichbar **und** ein Ausgang offen, wird abgelehnt. Dann Regelauswertung mit Verbotsvorrang. Ergebnis: `erlaubt`, `abgelehnt` oder `freigabepflichtig`.
4. **Freigeben, falls nötig.** Freigabeanfrage mit
   $$\text{approval\_key} = \mathrm{HMAC}_{k}\big(\text{tool} \,\|\, \mathrm{canon}(\text{args}) \,\|\, \text{tenant} \,\|\, \text{run\_id}\big)$$
   Benachrichtigung an den Menschen, Lauf wird angehalten. Nach Erteilung Verbrauch mit Zähler 1.
5. **Festschreiben.** Eine Transaktion über: Freigabeverbrauch, Audit-Eintrag der Entscheidung, Zustandswechsel des Laufs, Idempotenzschlüssel. Entweder alles oder nichts — genau das, was über neun Dateien hinweg heute unmöglich ist.
6. **Ausführen.** Der Adapter in `domain/`, `knowledge/`, `skills/` oder `llm/` läuft. Vorher Nachschlagen des Idempotenzschlüssels
   $$\text{idem\_key} = \mathrm{HMAC}_{k}\big(\text{run\_id} \,\|\, \text{seq} \,\|\, \text{tool} \,\|\, \mathrm{canon}(\text{args})\big)$$
   Existiert er in `executed_steps`, wird das gespeicherte Ergebnis zurückgegeben statt erneut auszuführen.
7. **Ergebnis festhalten.** Audit-Eintrag des Ergebnisses. Laufmarkierungen werden monoton gesetzt: fremde Inhalte verarbeitet, Ausgang verfügbar, private Daten berührt. Markierungen werden **nie** zurückgenommen.
8. **Zurückgeben.** Antwort an `surface/`, Kennzahlen und Budgetverbrauch in `ops/`.

**Warum Schritt 6 den Idempotenzschlüssel braucht:** Das Zielkonzept räumt selbst ein, dass Werkzeugaufrufe beim Fortsetzen mindestens einmal erfolgen. Bei lesenden Aufrufen ist das harmlos, bei unumkehrbaren nicht — ein zweimal gesendeter Befehl an ein Gerät ist ein zweiter Befehl. Der Schlüssel macht aus „mindestens einmal" „genau einmal" für alles, was ihn trägt [HYPOTHESE zur Wirksamkeit, bis der Test in 5.5 läuft].

### 5.3 Datenmodell — Entwurf

**Dies ist ein Entwurf.** Die Tabellennamen des Bestands sind nicht erhoben, die Spaltenlisten sind aus Zweck und Zielkonzept abgeleitet, nicht aus dem Code gelesen. Vor der Umsetzung ist jede Tabelle gegen den heutigen Bestand abzugleichen.

**Kern und Nachweis**

| Tabelle | Zweck | Besonderheit |
|---|---|---|
| `meta` | Schemaversion, Instanzkennung | ein Eintrag je Schlüssel |
| `tenants` | Mandanten | Trennungsgrundlage |
| `principals` | Menschen, Agenten, Fremdsysteme | `kind` unterscheidet sie |
| `credentials` | Zugangsmittel | nur Commitment plus Salz, kein Geheimnis im Klartext |
| `revoked_tokens` | Sperrliste | aus `revoked_tokens.db` |
| `runs` | ein Lauf | `flag_untrusted`, `flag_private`, `flag_egress` monoton; Kosten und Tokens |
| `run_steps` | ein Schritt | `args_commit`, `decision`, `risk`, `approval_key`, `idem_key` |
| `executed_steps` | Idempotenz | Schlüssel ist Primärschlüssel |
| `tool_registry` | Werkzeugvertrag | `effect_class`, `reversible`, `risk_default`, Schema, `pinned_hash` |
| `policy_rules` | Regeln | `effect` ∈ {erlauben, verbieten}; `origin` ∈ {Mensch, Lernvorschlag}; Verbot gewinnt unabhängig von der Reihenfolge |
| `approvals` | Freigaben | `approval_key` Primärschlüssel, `uses_max`, `uses_count`, `expires_utc` |
| `audit_chain` | Kette | nur Anfügen über Trigger; `prev_hash`, `entry_hash` |
| `audit_payload` | Nutzdaten | `salt` löschbar → Krypto-Schreddern |
| `audit_anchors` | externe Anker | Kettenkopf, Verfahren, Beweis |
| `budgets` | Kosten- und Aufrufgrenzen | je Mandant und Zeitfenster |

**Arbeit**

| Tabelle | Zweck | Besonderheit |
|---|---|---|
| `tasks`, `task_deps` | Aufgaben und Abhängigkeitsgraph | aus `tasks.db` |
| `queue_leases` | Bearbeitungsrechte | `version`-Spalte für optimistische Nebenläufigkeit |
| `workers`, `handoffs` | Arbeiter und Übergaben | |
| `schedules` | Zeitplan | aus `scheduler.db`; speichert `args_commit`, nicht rohe Argumente |

**Wissen**

| Tabelle | Zweck | Besonderheit |
|---|---|---|
| `facts` | Fakten, **bi-temporal** | `valid_from`/`valid_to` = wann wahr; `recorded_from`/`recorded_to` = wann geglaubt |
| `facts_fts` | Suche | FTS5 mit externem Inhalt über `facts`, per Trigger gepflegt |
| `lessons` | Lektionen | `origin`, `accepted_by`, `expires_utc`, `hits`, `misses` — nur mit menschlicher Übernahme wirksam |
| `history` | Verlauf | aus `history.db` |
| `traces` | Spuren | aus `traces.db`; `run_id` verbindet sie mit dem Audit |
| `evals` | Messwerte | Suite, Metrik, Wert |

**Fähigkeiten**

| Tabelle | Zweck | Besonderheit |
|---|---|---|
| `skills` | Verzeichnis | `tier` ∈ {1, 2, 3}, `content_hash` festgeschrieben |
| `macros` | Ablaufmakros | `steps_json`, `params_json` — **Daten, kein Code** |

**Zwei Modellierungsentscheidungen mit Begründung.** Erstens bi-temporale Fakten statt einfachem Zeitstempel: ohne die zweite Zeitachse lässt sich „das war damals unsere Annahme" nicht von „das war damals wahr" unterscheiden, und genau diese Unterscheidung braucht ein Replay, das alte Läufe fair nachspielt. Zweitens Argument-Commitments statt roher Argumente in `schedules` und `run_steps`: ein Zeitplan, der rohe Argumente speichert, ist eine dauerhafte Kopie möglicherweise personenbezogener Daten außerhalb des löschbaren Bereichs.

### 5.4 Modulumzug

Zuordnung der größten Bestandsteile. Vollständige Zuordnung aller 291 Module ist Teil von Schnitt R5.

| Heute | Ziel | Hinweis |
|---|---|---|
| `gateway/simple_*` (12) | `kernel/http/` | wird der einzige Pfad (A1) |
| `gateway/server.py`, `routes_*` (7) | **entfällt** | nach Testportierung (A1) |
| `policy.py`, `security_policy.py` | `kernel/policy.py` | Ausbau nach A4, Ziel 95 % Abdeckung |
| `approval_store.py`, `security_authorization.py`, `task_security.py` | `kernel/approval.py`, `kernel/authz.py` | Argumentbindung nach A5 |
| `gateway/auth.py`, `csrf.py`, `secrets.py`, `security.py`, `rate_limit.py` | `kernel/` | unverändert übernehmen, Abdeckung ist gut |
| *neu* | `kernel/audit.py` | A3, existiert nicht |
| `db_mixin.py`, `migrations/` | `store/` | eine Datei, ein Schema (A2) |
| `tasks.py`, `task_merge_service.py`, `task_merge_helpers.py`, `task_command_support.py`, `gateway/task_support.py` | `domain/tasks/` | sechs Module auf fachlichen Schnitt, je unter 600 Zeilen (A8) |
| `core/dag_engine.py`, `workers.py`, `worker_handoff_service.py`, `scheduler.py`, `gateway/queue_security.py` | `domain/` | `queue_security` von 46 % auf Schwelle |
| `memory/`, `reflection_memory.py`, `history*.py` | `knowledge/` | `hybrid.py` und `graph.py` prüfen oder ersetzen (F3) |
| `skills/manager.py`, `runtime.py`, `ldf.py`, `agent_skills.py` | `skills/` | A6, Ziel null Skripte |
| `llm/` | `llm/` | Auswahl und Ausweichpfad ergänzen (F6) |
| `evals.py`, `security/replay_*`, `security/governance.py`, `monitoring.py`, `metrics.py`, `tracing.py`, `observability.py`, `core/self_healing.py` | `ops/` | `evals.py` 1.671 Zeilen aufteilen |
| `cli*.py`, `web/`, `channels.py`, `__main__.py`, `dashboard_support.py`, zehn weitere `*_support.py` | `surface/` | `__main__.py` 1.014 Zeilen entflechten |
| `consensus.py`, `voice/`, `llm/context.py`, `release_smoke.py`, `a2a_pilot.py` | **Entscheidung in R6** | alle 0 % oder ohne Testmodul |
| `bundled_assets/` | **entfällt** | identisch zu `bundled/` |

### 5.5 Fehlermodi und Gegenmaßnahmen

| Nr | Fehlermodus | Wirkung | Gegenmaßnahme | Prüfung |
|---|---|---|---|---|
| E1 | Abbruch zwischen Freigabeverbrauch und Ausführung | Freigabe verbraucht, nichts geschehen, oder Ausführung ohne Protokoll | eine Transaktion über Verbrauch, Audit und Zustandswechsel (A2, A5) | erzwungener Abbruch an jeder Nahtstelle, Nachprüfung der Invarianten |
| E2 | Doppelausführung beim Fortsetzen | unumkehrbare Wirkung zweimal | Idempotenzschlüssel (5.2) | Lauf abbrechen und fortsetzen, Wirkung genau einmal |
| E3 | Lernschleife hebt Verbot auf | stiller Rechteausbau | Verbotsvorrang (A4) | Permutationstest über Regelreihenfolgen |
| E4 | Freigabe deckt fremden Aufruf | Freigabe für X erlaubt Y | Argumentbindung (A5) | Freigabe für X, Aufruf Y wird abgelehnt |
| E5 | Prompt-Injection über Werkzeugergebnis | Agent handelt im Interesse Dritter | monotone Laufmarkierungen, Ausgangsliste (A4) | bösartiger Agenten-Doppelgänger, Vollaufzählung bis Länge 6 |
| E6 | Injektion über Skill-Skript | Codeausführung außerhalb des Engpasses | drei Stufen, Hash-Festschreibung (A6) | nicht gelistetes und abweichendes Skript werden abgelehnt |
| E7 | Audit nachträglich verändert | Beweiskraft weg | Nur-Anfügen-Trigger, Kette, externer Anker (A3) | Feldänderung lässt Prüfung fehlschlagen |
| E8 | Löschpflicht gegen unveränderliche Kette | rechtliches Risiko oder kaputte Kette | Commitment in der Kette, Nutzdaten samt Salz löschbar (A3) | nach Löschen Kette grün, Eintrag gilt als redigiert |
| E9 | Schreibstau in einer Datei | Zeitüberschreitungen | WAL, `busy_timeout`, kurze Transaktionen, Stapelschreiben (A2) | Lastlauf mit gleichzeitigen Schreibern |
| E10 | Unsichere Vorgabe im Betrieb | offene Endpunkte | sichere Vorgaben, Starterweigerung (A7) | Vorgabetest ohne Konfiguration |
| E11 | Erneuter Modulzerfall | Zustand von heute kehrt zurück | Importregeln, Zeilengrenze, Namensverbot (A8) | Syntaxbaum-Test |
| E12 | Migration verliert Daten | Verlust bei der Zusammenlegung | Vorwärtsmigration mit Sicherung, Zeilenzahlvergleich, Goldstand-Datenbanken (A2) | Migration jeder der neun Altformen, Zeilen vorher und nachher gleich |
| E13 | Plattformunterschied Windows gegen Linux | grün hier, kaputt dort | CI-Matrix über Linux, macOS, Windows; FTS5-Erkennung beim Start | Suite auf allen drei; Startabbruch bei fehlendem FTS5 |

### 5.6 Trade-offs der Zielarchitektur als Ganzes

| Eigenschaft | Heute | Ziel | Richtung |
|---|---|---|---|
| Angriffsfläche HTTP | zwei Pfade, 2.783 Anweisungen | ein Pfad, 1.610 | besser |
| Fremdabhängigkeit im Kern | `fastapi` | keine | besser |
| Transaktion über Fachbereiche | unmöglich | gegeben | besser |
| Manipulationserkennung im Protokoll | keine | Kette plus Anker | besser |
| Schreibdurchsatz | neun unabhängige Schreiber | ein Schreiber | schlechter, beherrschbar |
| Falschablehnungen | wenige | mehr | schlechter, gewollt |
| Funktionsumfang HTTP | WebSocket | SSE | schlechter, ausreichend |
| Zeilen im Paket | 47.330 | geschätzt 30.000 bis 34.000 nach R1 bis R6 | besser [HYPOTHESE] |
| Abdeckung Entscheidungspunkt | 0 % | mindestens 95 % | besser |
| Prüfbarkeit durch Dritte | elf konkurrierende Pläne | ein Architekturdokument, ein Changelog | besser |

---

## 6 Funktionskatalog: Was BotBoy fertig gebaut leistet

Je Funktion: Zustand heute, Ziel, maschinelle Abnahme. **F-Nummern sind Fertigstellungsarbeiten**, sie folgen nach dem Aufräumen aus Kapitel 7.

### 6.1 Kern

| Nr | Funktion | Zustand | Ziel | Abnahme |
|---|---|---|---|---|
| F1 | Entscheidungspunkt | `policy.py` 0 % | A4 vollständig, Abdeckung ≥ 95 % mit Zweigen | Permutationstest, Vollaufzählung bis Länge 6, Ausnahme führt zur Ablehnung |
| F2 | Nachweiskette | existiert nicht | A3 mit externem Anker | Feldänderung erkannt, Anker offline prüfbar, Löschung lässt Kette gültig |
| F1b | Freigaben | bereichsweit | A5 argumentgebunden, einmalig, befristet | Freigabe X deckt Y nicht, Zweitverwendung abgelehnt |
| F1c | Sichere Vorgaben | `enable_auth=False` | A7 | Vorgabetest, Starterweigerung |
| F1d | Idempotenz | fehlt | Schlüssel je Schritt | Abbruch und Fortsetzen wirkt genau einmal |
| F1e | Budgets | `budgets`-Tabelle fehlt | Kosten- und Aufrufgrenzen je Mandant und Fenster, Überschreitung lehnt ab | Grenzlauf wird abgelehnt und protokolliert |
| F1f | Halt-Schalter | nicht belegt | ein Schalter hält alle Läufe sofort an, fail closed | Test setzt Schalter, laufender Schritt endet, neuer wird abgelehnt |
| F1g | Ausgangsliste | nicht belegt | Erlaubnisliste für ausgehende Ziele, Vorgabe leer | nicht gelistetes Ziel wird abgelehnt |

### 6.2 Arbeit

| Nr | Funktion | Zustand | Ziel | Abnahme |
|---|---|---|---|---|
| FW1 | Aufgaben und Graph | vorhanden, sechsfach geschnitten | ein Fachpaket, Module unter 600 Zeilen | Importtest, Zeilentest, Verhalten unverändert |
| FW2 | Warteschlange | `queue_security.py` 46 % | ≥ 85 %, optimistische Nebenläufigkeit über `version` | zwei gleichzeitige Schreiber, genau einer schreibt fest |
| FW3 | Arbeiter und Übergabe | vorhanden | unverändert, umgezogen | Suite grün |
| FW4 | Zeitplan | vorhanden | speichert Commitments statt roher Argumente | Test prüft, dass keine rohen Argumente persistiert werden |

### 6.3 Wissen und Lernen

| Nr | Funktion | Zustand | Ziel | Abnahme |
|---|---|---|---|---|
| F3 | Gedächtnis | `memory/hybrid.py` 0 %, `graph.py` 0 % | bi-temporale Fakten auf FTS5, Goldset mit Recall-Messung | Goldset-Lauf erreicht die festgelegte Schwelle, erst danach gilt die Funktion als fertig |
| F3b | Deutsche Normalisierung | FTS5 bringt keinen deutschen Stemmer | eigener Normalisierer vor der Indizierung | Testvektoren mit Umlauten, Komposita, Flexion |
| F5 | Lernschleife | `reflection_memory.py` 44 %, Übernahmeweg unbelegt | Vorschlag, Wirkungsmessung per Replay, Übernahme nur durch Menschen | ein Test weist nach, dass ohne Übernahme keine Regel und keine Lektion wirkt |
| F5b | Replay-Prüfstand | `replay_harness.py` 62 %, `replay_diff.py` 85 % — **existiert teilweise** | Abweichung gilt als unbekannt, nie als Erfolg; Sicherheitstor hart | Mutationstest: eingebaute Verschlechterung wird erkannt |
| F5c | Lektionen mit Verfall | fehlt | Einzelposten mit Herkunft, Verfall, Treffer- und Fehlzählern | Test prüft Verfall und Zählerführung |

**Zur Ehrlichkeit bei F3:** Die Messlage zu Gedächtnissystemen ist schlecht. Eine Prüfung des gängigen Vergleichsdatensatzes fand 6,4 % falsche Musterlösungen, und der Standardbewerter akzeptierte 62,81 % absichtlich falscher, themennaher Antworten [ETABLIERT, Sekundärquelle]. Gleichzeitig ist BM25 ein starker Vergleichswert, und ein Anbieter ersetzte 2026 seinen Graphspeicher wieder durch BM25 mit Einbettungen [ETABLIERT]. Daraus folgt: **kein Umbau des Gedächtnisses ohne eigenes Goldset.** Der FTS5-Weg bleibt, bis eine eigene Messung etwas anderes zeigt. Das Argument für ein optionales Einbettungs-Backend ist nicht „Graphen sind besser", sondern allein die fehlende deutsche Sprachbehandlung in FTS5.

### 6.4 Fähigkeiten

| Nr | Funktion | Zustand | Ziel | Abnahme |
|---|---|---|---|---|
| F4 | MCP-Server | vorhanden, `test_mcp_http_security` | gegen Revision 2026-07-28 geprüft: `server/discover`, Mehrfachrundenanfragen mit `input_required` und einmal verbrauchbarem `requestState`, Pflichtkopfzeilen `Mcp-Method` und `Mcp-Name`, Fehlercode `-32020` als `HeaderMismatch`, zustandslos | Prüfmatrix über alle MUSS-Aussagen der Spezifikation; Zusammenspiel gegen die Referenz-SDKs |
| F4b | MCP-Client | unklar, ob OAuth-geschützte Fremdserver nutzbar | Fremdserver mit Prüfung von `iss`; `sampling` von Fremdservern wird fail closed abgelehnt; Pinning bei **jedem** Aufruf, nicht nur bei Registrierung | Test mit Fremdserver-Doppelgänger, der nach Registrierung sein Werkzeug austauscht |
| F6 | Modellanbindung | `llm/integration.py` 49 %, `parallel_think.py` 26 %, `reflection.py` 30 % | Auswahl mit Ausweichpfad, Zeitgrenzen, Kostenzuordnung je Lauf | Ausfall des Hauptanbieters führt zu Ausweichen, nicht zum Abbruch; Kosten im Lauf sichtbar |
| F6b | Skills Stufe 1 und 2 | 105 Skills, 75 Skriptverzeichnisse | null Skripte im Paket, Makros als Daten | Bauartefakt ohne `scripts/`, Makro-Risiko gleich Maximum der Schritte |

### 6.5 Oberfläche

| Nr | Funktion | Zustand | Ziel | Abnahme |
|---|---|---|---|---|
| F7a | CLI | `cli.py` 21 %, `cli_dispatch.py` 31 %, `__main__.py` 1.014 Zeilen | entflochten, ≥ 70 % | Suite grün, Zeilengrenze eingehalten |
| F7b | Weboberfläche | vorhanden, WebSocket nur im FastAPI-Pfad | SSE über `http.server` | Anzeige aktualisiert sich ohne WebSocket |
| F7c | Kanäle und Benachrichtigung | `channels.py` ohne eigenes Testmodul | Freigabeanfrage erreicht den Menschen, Entscheidung kehrt zurück | Ende-zu-Ende-Test mit Doppelgänger-Kanal |
| F7d | Kennzeichnung | nicht belegt | ausgehende Nachrichten weisen auf maschinelle Herkunft hin | Test prüft Kennzeichen in jedem Kanal |

**Zu F7d:** Artikel 50 der KI-Verordnung gilt seit dem 02.08.2026; die Verordnung (EU) 2026/1744 vom 24.07.2026 gewährt nur für Artikel 50 Absatz 2 eine Schonfrist bis 02.12.2026 für Altsysteme. Die Ausnahme für offene Software nach Artikel 2 Absatz 12 greift bei Artikel 50 **nicht** [GESICHERT, Recherchestand 17.10.2026]. Ob ein Agenten-Laufzeitsystem ohne eigenes Modell überhaupt Anbieter ist, ist offen. Die Kennzeichnung ist billig und beseitigt die Frage — keine Rechtsberatung.

### 6.6 Später, bewusst nicht jetzt

| Nr | Funktion | Begründung der Verschiebung |
|---|---|---|
| F8 | Hexagon-Raum | Kein Code vorhanden, also Neubau statt Aufräumen. Die Forschungslage stützt: 3 bis 4 aktive Plätze, Rundenlimit 2 bis 3, blinde Erstvorschläge mit Offenlegungspflicht, stärkstes Modell als anonymisierter Integrator. Erst nach F1 und F2, weil jeder Platz Läufe durch den Engpass erzeugt und ohne Budgets (F1e) unbegrenzt kostet [ETABLIERT für die Forschungslage] |
| F9 | Geräte-Brücken | Der stärkste Beweis für eine Kontrollschicht, aber auch die größte unumkehrbare Wirkung. Erst nach F1d (Idempotenz), F1f (Halt-Schalter) und F2 (Nachweiskette). Belege zu Angriffen über Bedienoberflächenbäume mobiler Agenten sind deutlich [ETABLIERT] |
| F10 | Körper und Bühne | Kein Zusammenhang mit dem Kernversprechen. Ein Ausbau in Richtung räumlicher Standards lohnt nach der Prüfung vom 17.10.2026 nicht |
| F11 | Agent-zu-Agent | `a2a_pilot.py` vorhanden, aber undurchsichtige Fremdausführung widerspricht P1. Zurückstellen; MCP deckt Fremdagenten ab |
| F12 | Sprache, weitere Kanäle | `voice/listener.py` 0 %, Pakete nicht importiert. Entfernen statt pflegen (R6), Wiederaufnahme nur bei konkretem Bedarf |
---

## 7 Aufräumplan

Neun Schnitte. Jeder ist als Vorbedingung, Aktion, Nachbedingung formuliert, damit ein Modell ihn ohne Auslegungsspielraum ausführen kann. **Nach jedem Schnitt muss die Suite grün sein.** Reihenfolge ist bindend: sie steigt in Risiko und fällt in Umkehrbarkeit.

### R1 Abhängigkeitsliste auf die Wirklichkeit bringen — Aufwand S, Risiko niedrig

**Lage:** `setup.py` ist bereits richtig gestuft: `install_requires=["pyyaml>=6.0"]`, dazu die Zusatzgruppen `standard`, `full`, `dev`. `requirements.txt` listet dagegen alles flach als wäre es Pflicht. Von den dort genannten Paketen werden `uvicorn`, `websockets`, `aiohttp`, `httpx`, `psutil`, `docker`, `sentence-transformers`, `vosk` und `pyaudio` im Paket **nirgends importiert** [GESICHERT].

**Aktion:** `requirements.txt` ersetzen durch einen Zweizeiler, der auf die Zusatzgruppen in `setup.py` verweist. Die Gruppe `full` streichen, da keines ihrer vier Pakete importiert wird. Aus `standard` die nicht importierten Pakete entfernen; was bleibt, ist `fastapi` bis R5 greift. `pyyaml` aus `install_requires` in eine Gruppe `config` verschieben, denn es wird nur an drei Stellen lazy im Funktionskörper geladen — die Aufrufstellen müssen dann den Fehlerfall sauber behandeln.

**Nachbedingung:** Eine frische Umgebung mit ausschließlich den Pflichtabhängigkeiten startet BotBoy und beantwortet eine Statusanfrage.

**Verifikation:** `python -m venv` ohne Zusatzgruppen, Import des Pakets, Start, Statusabfrage · `pip install .` ohne Gruppen zieht kein PyTorch nach, geprüft über die Paketliste nach der Installation.

**Warum zuerst:** Es ist der billigste Schnitt mit der größten Außenwirkung. Wer heute `requirements.txt` folgt, installiert PyTorch samt Systembibliotheken für Funktionen, die der Code nicht aufruft.

### R2 Doppelten Skill-Baum entfernen — Aufwand S, Risiko niedrig

**Vorbedingung:** `diff -rq botboy/bundled botboy/bundled_assets` liefert keine Ausgabe — bereits geprüft, die Bäume sind byte-identisch mit je 131 Dateien [GESICHERT]. `MANIFEST.in` enthält `recursive-include botboy/bundled *`, **nicht** `bundled_assets` — der doppelte Baum wird also nicht einmal ausgeliefert [GESICHERT].

**Aktion:** `botboy/bundled_assets/` löschen. Alle Verweise im Code auf `bundled_assets` suchen und auf `bundled` umbiegen.

**Nachbedingung:** Kein Verweis auf `bundled_assets` mehr vorhanden, Suite grün, Bauartefakt unverändert im Inhalt.

**Verifikation:** `grep -r bundled_assets` liefert null Treffer · Suite grün · das gebaute Artefakt enthält dieselbe Dateiliste wie vorher, verglichen über sortierte Inhaltsverzeichnisse.

### R3 Restmüll im Wurzelverzeichnis entfernen — Aufwand S, Risiko niedrig

**Aktion:** `scratch_test.py` löschen. Es läuft in der Suite mit und enthält dieselbe Testklasse und denselben Test wie `tests/test_orchestrator_service_integration.py` [GESICHERT].

**Nachbedingung:** Die Testzahl sinkt um die doppelt gezählten Fälle, kein Testfall verschwindet. Vor dem Löschen wird die Fallliste beider Dateien verglichen; ein nur in `scratch_test.py` vorhandener Fall wandert nach `tests/`.

**Verifikation:** Fallnamen vor und nach dem Schnitt als Mengen verglichen, Differenz ist leer bis auf die Duplikate · Suite grün.

### R4 Dokumente konsolidieren — Aufwand S bis M, Risiko niedrig

**Lage:** Elf Planungsdokumente mit 4.172 Zeilen im Wurzelverzeichnis, zwei davon mit „V2_V7" und zwei mit „V2_V3" im Namen, während `docs/` nur zwei Dateien enthält. Kein `CHANGELOG.md`, keine Architekturdatei [GESICHERT].

**Aktion:** Zielzustand im Wurzelverzeichnis: `README.md`, `ARCHITEKTUR.md` (dieses Dokument), `CHANGELOG.md`, `SECURITY.md`, `LICENSE`, `CONTRIBUTING.md`. Alle elf Planungsdokumente nach `docs/archiv/` mit unverändertem Inhalt und einem Kopfhinweis „historisch, nicht mehr gültig, abgelöst durch ARCHITEKTUR.md vom 18.10.2026". Aus ihnen alle noch offenen Vorhaben in **einen** Rückstand `docs/backlog.md` überführen.

**Nachbedingung:** Im Wurzelverzeichnis liegt genau ein gültiges Architekturdokument und genau ein Rückstand.

**Verifikation:** Zählung der `*.md` im Wurzelverzeichnis ergibt höchstens sechs · jedes Dokument in `docs/archiv/` trägt den Kopfhinweis · `CHANGELOG.md` existiert und nennt `0.6.0-dev` als aktuellen Stand.

**Warum das kein Kosmetikpunkt ist:** Bei KI-gestützter Umsetzung liest das Modell diese Dokumente als Vorgaben. Elf widersprüchliche Pläne bedeuten, dass es sich einen aussucht.

### R5 Ein Gateway — Aufwand L, Risiko hoch

**Dies ist der härteste Schnitt.** Reihenfolge innerhalb von R5 ist bindend:

1. **Zuerst portieren, dann löschen.** Die elf Testmodule, die heute über `fastapi.testclient` laufen, erhalten ein Äquivalent auf dem Standardbibliotheks-Pfad: `test_final_execution_authorization`, `test_gateway_auth_security`, `test_execution_tenant_approval_integration`, `test_merge_security_boundary`, `test_trace_tenant_security`, `test_scheduler_execution_boundary`, `test_task_store_mutator_boundary`, `test_worker_read_boundary`, `test_websocket_tenant_integration`, `test_sqlite_lifecycle`, `test_gateway_identity_integration` [GESICHERT als Liste].
2. **Rückrichtung klären.** Prüfen, ob die Weboberfläche die bidirektionale Verbindung tatsächlich braucht. Falls nein: Server-Sent Events über `http.server`. Falls ja: Entscheidung neu aufrufen, nicht heimlich eine WebSocket-Eigenimplementierung bauen.
3. **Dann löschen:** `gateway/server.py`, `routes_core.py`, `routes_tasks.py`, `routes_ws.py`, `routes_auth.py`, `routes_fleet.py`, `routes_orgs.py`, der `fastapi`-Zweig in `errors.py`, die Zusatzgruppe `standard` in `setup.py`.

**Nachbedingung:** Die vollständige Suite läuft in einer Umgebung **ohne** installiertes `fastapi` grün.

**Verifikation:** `grep -rE '^\s*(from|import)\s+fastapi'` liefert null Treffer · frische Umgebung ohne `fastapi`, Suite grün · Abdeckung des verbleibenden Gateway-Pfads nicht unter 79 % · für jede zuvor geprüfte Grenze existiert ein Testmodul auf dem neuen Pfad, nachgewiesen über eine Zuordnungstabelle alt zu neu.

**Abbruchkriterium:** Wenn sich nach drei Portierungen zeigt, dass der Standardbibliotheks-Pfad eine geprüfte Grenze nicht abbilden kann, wird R5 angehalten und A1 neu entschieden — nicht die Grenze aufgegeben.

### R6 Entscheidung über ungeprüfte Randmodule — Aufwand M, Risiko mittel

**Lage:** 1.131 Anweisungen mit 0 % Abdeckung [GESICHERT]. Drei Gruppen mit drei verschiedenen Antworten:

| Modul | Anweisungen | Entscheidung | Begründung |
|---|---|---|---|
| `policy.py` | 62 | **prüfen** (F1) | Entscheidungspunkt, Ziel 95 % |
| `security/sandbox.py` | 54 | **prüfen oder entfernen** | nur behalten, wenn F9 oder A6 Stufe 3 es braucht |
| `security/tenant_sandbox.py` | 26 | **prüfen** | Mandantentrennung ist Kernversprechen |
| `skills/ldf.py` | 305 | **prüfen** (A6) | Torwächter der Skill-Ausführung |
| `memory/hybrid.py` | 237 | **prüfen oder ersetzen** (F3) | erst Goldset, dann entscheiden |
| `memory/graph.py` | 62 | **entfernen** | Graphspeicher ohne Messnachweis; Anbieter sind 2026 davon abgerückt |
| `core/state.py`, `core/sync.py` | 93 | **prüfen** | Zustandsführung |
| `consensus.py` | 63 | **entfernen** | ohne Nutzung; Wiederaufnahme mit F8 |
| `voice/listener.py` | 88 | **entfernen** | `vosk`/`pyaudio` nicht importiert, 0 % |
| `llm/context.py` | 46 | **entfernen** | ohne Nutzung |
| `release_smoke.py` | 93 | **entfernen** | `release_acceptance.py` deckt den Zweck |
| `a2a_pilot.py` | 273 | **entfernen** | widerspricht P1; MCP deckt Fremdagenten (F11) |

**Regel für diesen Schnitt:** Entfernen ist billiger als Prüfen, solange kein Funktionsziel aus Kapitel 6 das Modul verlangt. Jede Entfernung geht mit eigenem Commit und Eintrag in `CHANGELOG.md`, damit sie ohne Archäologie rückholbar ist.

**Verifikation:** Nach R6 liegt die Zahl der Anweisungen mit 0 % Abdeckung unter 200 · Suite grün · jede Entfernung hat einen eigenen Commit.

### R7 Modulumzug und Importschranken — Aufwand L, Risiko mittel

**Aktion:** Umzug nach der Zuordnung in 5.4 in die acht Pakete aus A8. Danach drei Prüfungen einführen: Importrichtung über den Syntaxbaum, Zeilengrenze 600, verbotene Dateinamensendungen `_support`, `_helpers`, `_service`.

**Reihenfolge innerhalb von R7:** erst `store/` und `kernel/`, weil alle anderen davon abhängen; dann `domain/`, `knowledge/`, `skills/`, `llm/`; zuletzt `ops/` und `surface/`. Nach jedem Paket ein Commit mit grüner Suite.

**Nachbedingung:** Kein Modul über 600 Zeilen. `kernel/` importiert nichts außer `store/`. Die sechs Aufgaben-Module sind fachlich geschnitten.

**Verifikation:** die drei Prüfungen sind Teil der Suite und grün · Abdeckung gesamt nicht unter dem Wert vor R7 · Verhalten unverändert, nachgewiesen über die unveränderte Suite.

**Risiko und Gegenmaßnahme:** Ein Umzug dieser Größe erzeugt einen Unterschied, den niemand zeilenweise prüft. Gegenmaßnahme: ausschließlich Verschiebungen und Importanpassungen, **keine** inhaltliche Änderung im selben Commit. Wer im Umzug refaktoriert, verliert die Aussagekraft der grünen Suite.

### R8 Eine Datenbank — Aufwand L, Risiko mittel

**Aktion:** Umsetzung von A2. `_SQLiteMixin` erhält einen gemeinsamen Pfad, die Tabellen werden nach 5.3 benannt, `migrations/runner.py` wird der einzige Weg zu Schemaänderungen. Vorwärtsmigration mit Sicherung der Altdateien.

**Nachbedingung:** Die Anwendung öffnet genau eine Datei. Aufgabe, Freigabe und Protokolleintrag sind in einer Transaktion schreibbar.

**Verifikation:** Zählung der angelegten Datenbankdateien im Testlauf ergibt eins · Transaktionstest mit erzwungenem Abbruch rollt alle drei Teile zurück · Migrationstest je Altform mit Zeilenzahlvergleich vorher und nachher · Lastlauf mit gleichzeitigen Schreibern ohne Zeitüberschreitung bei gesetztem `busy_timeout`.

**Zwingende Voraussetzung:** R8 kommt **vor** F2. Eine Nachweiskette, die nicht in derselben Transaktion wie die Entscheidung geschrieben wird, beweist das Falsche.

### R9 Skills auf Stufe 1 und 2 bringen — Aufwand L, Risiko mittel

**Lage:** 105 Skills, 75 `scripts/`-Verzeichnisse; `MANIFEST.in` liefert `skills` und `examples/skills` mit aus, Skripte landen also im Paket [GESICHERT].

**Aktion:** Jedes Skriptverzeichnis einzeln einordnen. Reine Anleitung ohne Code wird Stufe 1. Eine Folge von Werkzeugaufrufen wird Stufe 2, also ein Ablaufmakro aus Daten mit Parameterfeldern. Was beides nicht ist, geht in die Erlaubnisliste der Stufe 3 mit festgeschriebenem Inhalts-Hash und erzwungener Freigabe, versehen mit Auslauffrist. Ziel ist null Skripte im ausgelieferten Paket.

**Verifikation:** Bauartefakt ohne `scripts/`-Verzeichnis · `skills/ldf.py` und `skills/manager.py` mindestens 90 % · nicht gelistetes Skript und gelistetes mit abweichendem Hash werden abgelehnt · Makro-Risiko gleich dem Maximum seiner Schritte · Import eines Skills mit Skript wird abgelehnt.

### 7.1 Erwartete Wirkung des Aufräumens

| Größe | Vorher (gemessen) | Nachher (Schätzung) | Quelle der Schätzung |
|---|---|---|---|
| Python-Zeilen | 47.330 | 30.000 bis 34.000 | R2 doppelter Baum, R5 rund 1.173 Anweisungen, R6 rund 900 Anweisungen, R9 Skriptwegfall [HYPOTHESE] |
| Module | 291 | 180 bis 210 | R2, R5, R6, R7-Bündelung [HYPOTHESE] |
| Harte Fremdabhängigkeiten | 1 (`fastapi`) plus `pyyaml` als Pflicht | 0 | R1, R5 [GESICHERT in der Richtung] |
| HTTP-Pfade | 2 | 1 | R5 [GESICHERT] |
| Datenbankdateien | 9 | 1 | R8 [GESICHERT in der Richtung] |
| Markdown im Wurzelverzeichnis | 11 Pläne, 4.172 Zeilen | 6 Dateien, ein gültiger Plan | R4 [GESICHERT] |
| Anweisungen mit 0 % Abdeckung | 1.131 | unter 200 | R6, F1 [HYPOTHESE] |
| `scripts/`-Verzeichnisse | 75 | 0 im Paket | R9 [HYPOTHESE] |

Die Zeilenschätzung ist bewusst eine Spanne und **nicht** das Ziel. Weniger Zeilen sind hier Nebenwirkung von weniger Doppelung, nicht Zweck.

---

## 8 Fahrplan

Acht Phasen. Jede hat ein Tor, das maschinell prüfbar ist, und ein Abbruchkriterium. Die Zeitangaben sind **[HYPOTHESE]** bei KI-gestützter Umsetzung und einem Entwickler, der Ergebnisse prüft und entscheidet, aber nicht selbst programmiert.

| Phase | Inhalt | Tor | Abbruchkriterium |
|---|---|---|---|
| **0** Tage 1–3 | R1, R2, R3, R4 | Suite grün · höchstens 6 `*.md` im Wurzelverzeichnis · frische Umgebung mit Pflichtabhängigkeiten startet · `grep bundled_assets` leer | keines, alle Schnitte sind trivial umkehrbar |
| **1** Woche 1–2 | R5, R6 | Suite ohne `fastapi` grün · Gateway-Abdeckung ≥ 79 % · Zuordnungstabelle alt zu neu vollständig · Anweisungen mit 0 % unter 200 | eine geprüfte Grenze ist auf dem Standardbibliotheks-Pfad nicht abbildbar → A1 neu entscheiden |
| **2** Woche 3–4 | R7, A7 (= F1c) | drei Strukturprüfungen grün · kein Modul über 600 Zeilen · Vorgabetest grün, Suite ohne Sicherheitswarnung | Abdeckung sinkt durch den Umzug um mehr als 2 Punkte → Umzug zurücknehmen und in kleineren Schritten wiederholen |
| **3** Woche 5–7 | R8, F1, F1b, F1d | genau eine Datenbankdatei · `policy.py` ≥ 95 % mit Zweigen · Permutationstest grün · Vollaufzählung bis Länge 6 ohne Treffer · Freigabe X deckt Y nicht · Abbruch-und-Fortsetzen wirkt genau einmal | Vollaufzählung findet einen Weg, den die Policy nicht schließt → A4 erweitern, Phase 4 wartet |
| **4** Woche 8–10 | F2, F1e, F1f, F1g | Feldänderung in der Kette wird erkannt · Anker offline prüfbar · Löschung lässt Kette gültig · Budgetüberschreitung lehnt ab · Halt-Schalter stoppt · nicht gelistetes Ausgangsziel abgelehnt | Kettenprüfung kostet bei realistischer Protokollgröße mehr als eine Sekunde → Prüfung abschnittsweise statt vollständig |
| **5** Woche 11–13 | R9, F4, F4b, F6 | Bauartefakt ohne `scripts/` · MCP-Prüfmatrix über alle MUSS-Aussagen grün · Zusammenspiel mit den Referenz-SDKs · Anbieterausfall führt zu Ausweichen | ein Skill ist weder als Anleitung noch als Makro darstellbar und fachlich unverzichtbar → Stufe 3 mit Frist, nicht unbefristet |
| **6** ab Woche 14 | F3, F3b, F5, F5b, F5c | Goldset-Lauf erreicht die Schwelle · Mutationstest erkennt eingebaute Verschlechterung · ohne menschliche Übernahme wirkt keine Regel und keine Lektion | Goldset zeigt keinen Vorteil des Umbaus → FTS5 bleibt, Phase 6 endet hier |
| **7** danach | F8 Hexagon, F9 Geräte | je eigenes Konzept, erst nach grünem Tor 4 | Budgets oder Idempotenz nicht belegt → nicht beginnen |

### 8.1 Abhängigkeiten zwischen den Arbeiten

```
R1 ─┐
R2 ─┼─► R4 ──► R5 ──► R6 ──► R7 ──► R8 ──► F2 ──► F1e,F1f,F1g ──► F8, F9
R3 ─┘                               │        ▲
                                    └► F1 ───┤  (Policy braucht die Pakete,
                                       F1b   │   Audit braucht die eine DB)
                                       F1d ──┘
A7 ──► unabhängig, so früh wie möglich
R9 ──► nach F1 (Makro-Risiko braucht den Entscheidungspunkt)
F3 ──► nach R8 (bi-temporale Tabellen im gemeinsamen Schema)
F4 ──► unabhängig von R8, aber nach R5 (ein Pfad)
```

Zwei Reihenfolgen sind nicht verhandelbar: **R8 vor F2**, weil eine Kette ohne gemeinsame Transaktion das Falsche beweist, und **F1 vor R9**, weil das Risiko eines Makros ohne Entscheidungspunkt nicht bestimmbar ist.

### 8.2 Prüfkonzept: Schwellen je Zone statt global

Eine globale Abdeckungszahl verdeckt genau den Befund aus 2.6. Deshalb Zonen mit eigenen Schwellen, in der Integration erzwungen:

| Zone | Module | Schwelle | Begründung |
|---|---|---|---|
| Entscheidung | `kernel/policy.py` | 95 % Anweisungen **und** Zweige | trägt das ganze Versprechen |
| Nachweis | `kernel/audit.py`, `kernel/approval.py` | 95 % | Beweiskraft |
| Durchsetzung | übriges `kernel/` | 90 % | heute schon 80 bis 100 % |
| Fachlogik | `domain/`, `knowledge/`, `skills/` | 80 % | `queue_security` von 46 % anheben |
| Oberfläche und Betrieb | `surface/`, `ops/`, `llm/` | 60 % | niedriger Schaden bei Fehlern |
| Gesamt | alles | 75 % | heute 64 % |

Dazu vier Prüfungen, die Abdeckung allein nicht liefert:

1. **Mutationsprüfung** der Tests für Entscheidung, Freigabe und Nachweis. Ziel mindestens 90 % getötete Mutanten. Eine Mutationsprüfung findet genau den Fehler, den 395 grüne Tests bei 0 % Abdeckung des Entscheidungspunkts nicht finden können.
2. **Eigenschaftstests** für Normalisierung von Argumenten, Regelreihenfolge und Idempotenz.
3. **Angriffstests** mit einem absichtlich bösartigen Agenten-Doppelgänger, Vollaufzählung der Effektsequenzen bis Länge 6.
4. **Plattformmatrix.** Die Integration prüft bereits `windows-2022` und `ubuntu-24.04` mit Python 3.10 und 3.13 [GESICHERT] — das ist mehr als der Vorbericht annahm. Zu ergänzen: macOS, ein Abdeckungstor und eine FTS5-Erkennung beim Start, da die Python-Unterlagen FTS5 nicht zusichern.

---

## 9 Veröffentlichung, Lizenz, Lieferkette

### 9.1 Korrektur gegenüber dem Vorbericht

Der Bericht vom 17.10.2026 empfahl, erst nach einem Tor am Tag 30 zu veröffentlichen. **Das Repo ist bereits öffentlich** [GESICHERT]. Die Frage ist damit nicht mehr „wann veröffentlichen", sondern „wie mit einem laufenden öffentlichen Repo umgehen". Drei Folgen:

1. **Es gibt keine `LICENSE`-Datei** und in `setup.py` kein Lizenzfeld [GESICHERT]. Öffentlich ohne Lizenz heißt: alle Rechte vorbehalten. Niemand darf den Code nutzen, einbinden oder weitergeben, über das Ansehen und Forken innerhalb von GitHub hinaus. Wer Verbreitung will, muss eine Lizenz hinzufügen; wer Kontrolle will, kann es lassen, sollte die Wirkung aber kennen.
2. **Die Historie ist bereits veröffentlicht.** Ein später hinzugefügtes Geheimnis lässt sich nicht durch Löschen heilen, sondern nur durch Rotation. Mein Scan betraf **nur den aktuellen Stand** — der Klon ist flach. Die vollständige Historie ist noch zu prüfen [GESICHERT als Einschränkung].
3. **Es gibt keine `SECURITY.md`** und keinen Meldeweg [GESICHERT]. Bei einem Projekt, dessen Versprechen Sicherheit ist, ist das die auffälligste Lücke nach außen.

### 9.2 Was bereits gut ist

| Punkt | Zustand |
|---|---|
| `.env.example` | enthält nur Variablennamen, keine Werte, inklusive der Platzhalter für Modellschlüssel [GESICHERT] |
| `.gitignore` | deckt `*.db`, `*.db-shm`, `*.db-wal`, `*.sqlite`, Laufzeitordner und Bauverzeichnisse [GESICHERT] |
| Geheimnisse im aktuellen Stand | kein Treffer für Schlüsselmuster und für `key`/`secret`/`token`-Zuweisungen mit echten Werten [GESICHERT, nur HEAD] |
| Integration | Matrix über `windows-2022` und `ubuntu-24.04`, Python 3.10 und 3.13 [GESICHERT] |
| Abhängigkeitsstufung | in `setup.py` korrekt angelegt [GESICHERT] |

### 9.3 Zu entscheiden und zu tun

| Nr | Punkt | Empfehlung | Begründung |
|---|---|---|---|
| V1 | Lizenz | **Apache-2.0** | Patentklausel, keine Markenrechte, Standard im Umfeld vergleichbarer Projekte. Alternativen: MIT (kürzer, ohne Patentklausel), AGPL-3.0 (Kopierschutz, aber Übernahmehemmnis), oder bewusst keine Lizenz, wenn Verbreitung nicht gewollt ist. Eine Lizenz kann Missbrauch nicht verbieten; dafür sorgen sichere Vorgaben (A7) und Haftungsausschluss [ETABLIERT] |
| V2 | Historie auf Geheimnisse prüfen | vollständige Historie holen und scannen, Treffer **rotieren**, nicht löschen | das Repo ist öffentlich, Löschen heilt nichts |
| V3 | `SECURITY.md` plus privater Meldeweg | anlegen | Sicherheitsprojekt ohne Meldeweg |
| V4 | Aktionen auf Commit-Hash festlegen | `actions/checkout@v6` und `actions/setup-python@v6` auf SHA | Lieferkette; Tags sind verschiebbar |
| V5 | Abdeckungstor in der Integration | Zonen aus 8.2 erzwingen | sonst bleibt der Befund aus 2.6 unbemerkt |
| V6 | Verteilungsname | klären | nach der Recherche vom 17.10.2026 ist `botboy` auf PyPI von einem fremden Projekt belegt, `botboyz` war frei [ETABLIERT, Stand 17.10.2026]. Ein Paketname ist nötig, bevor `console_scripts` als `botboy` ausgeliefert werden |
| V7 | Markenprüfung | offen | DPMA, EUIPO und TMview waren in der Recherche nicht abrufbar. Keine Rechtsberatung |
| V8 | `CHANGELOG.md` | anlegen, ab `0.6.0-dev` | derzeit ist von außen nicht erkennbar, was sich geändert hat |
| V9 | Kennzeichnung maschineller Herkunft | F7d umsetzen | Artikel 50 KI-Verordnung gilt seit 02.08.2026, die Ausnahme für offene Software greift dort nicht [GESICHERT]. Billig und beseitigt die Frage |
| V10 | Löschkonzept für Audit-Nutzdaten | A3 plus Hinweis in der Dokumentation | Hashes gelten nach den europäischen Aufsichtsleitlinien in Fassung vom 07.07.2026 als personenbezogen; der anerkannte Weg ist das Löschen von Salz und Nutzdaten [ETABLIERT] |

Alle rechtlichen Punkte sind Orientierung, keine Rechtsberatung.

---

## 10 Risiken, Korrekturen, offene Fragen

### 10.1 Was dieses Konzept am Vorbericht vom 17.10.2026 korrigiert

| Aussage im Vorbericht | Befund heute | Status |
|---|---|---|
| „nie real gelaufen", nur gegen Nachbildungen geprüft | 395 Tests grün, 2 übersprungen, 74 Subtests, 35 s | **widerlegt** [GESICHERT] |
| Testlage unbekannt, keine Zahlen | 64 % Abdeckung, 15.818 Anweisungen | **ersetzt durch Messung** |
| Stdlib-only als Prinzip, noch nicht geprüft | 2 von 291 Modulen mit Fremdimport; `setup.py` korrekt gestuft; `requirements.txt` falsch | **präzisiert** |
| „breit gebaut, an zwei Tagen veröffentlicht" | Repo zeigt Pläne von V1 bis V7 und laufende Commits; die Zweitagesliste stammt aus ARCHITEKTUR.md, nicht aus dem Repo | **gegenstandslos für dieses Repo** |
| Erste Maßnahme: echter Modell-Lauf (O-01) | bleibt gültig, aber nicht als Erstmaßnahme: der Entscheidungspunkt mit 0 % Abdeckung sticht | **neu eingeordnet** |
| Replay-Prüfstand bauen (O-12) | `security/replay_harness.py` und `replay_diff.py` existieren, 62 % und 85 % | **teilweise schon gebaut** |
| Keine Plattformmatrix angenommen | Integration prüft Windows und Linux, Python 3.10 und 3.13 | **widerlegt** [GESICHERT] |
| Veröffentlichung nach Tor am Tag 30 | Repo ist bereits öffentlich, ohne Lizenz | **überholt**, siehe 9.1 |
| Schema v7 als Migrationsziel | Schemaversionen des Repos nicht erhoben; neun Datenbanken ohne gemeinsame Versionierung | **ersetzt durch A2 und 5.3** |
| Konsistenzbefunde K1 bis K24 | gelten weiter als Mängel **der Dokumente**, nicht als Aussagen über diesen Code | **eingeschränkt gültig** |

Was aus dem Vorbericht **unverändert gilt:** der Faktencheck der sieben Rechercheberichte mit 44 belegten Fehlern, der externe Stand der Technik zu Prompt-Injection, MCP, Gedächtnis und Multi-Agent, die Marktlage samt dem Befund zum Microsoft Agent Governance Toolkit, sowie der Lizenz- und Compliance-Teil mit den Korrekturen aus 9.1.

### 10.2 Risiken dieses Vorhabens

| Nr | Risiko | Wahrscheinlichkeit | Wirkung | Gegenmaßnahme |
|---|---|---|---|---|
| X1 | R5 löscht geprüfte Grenzen, bevor Ersatz steht | mittel | Sicherheitsrückschritt bei grüner Suite | Portierungsreihenfolge und Zuordnungstabelle sind Tor; Abbruchkriterium in R5 |
| X2 | R7 verbirgt eine inhaltliche Änderung im Umzug | mittel | stiller Fehler, Suite bleibt grün | nur Verschiebungen pro Commit; Abdeckung vor und nach dem Umzug vergleichen |
| X3 | R8 verliert Daten | niedrig | Verlust von Verlauf und Spuren | Sicherung, Zeilenzahlvergleich, Migrationstest je Altform |
| X4 | Strengere Policy erzeugt zu viele Falschablehnungen | hoch | System wird unbenutzbar, Vorgaben werden aufgeweicht | Falschablehnungen messen, Regeln nachschärfen statt Grundhaltung aufgeben; Freigabeweg muss bequem sein (F7c) |
| X5 | Kapazität reicht nicht | hoch | Vorhaben bleibt in Phase 3 stehen | Phasen 0 bis 2 liefern allein schon Wert; jede Phase ist ein Haltepunkt |
| X6 | Plattformunterschied Windows | mittel | grün in der Integration, kaputt im Betrieb | macOS und FTS5-Erkennung ergänzen; der jüngste Commit betraf genau Windows |
| X7 | Zielkonzept und Code driften erneut auseinander | hoch | wieder elf konkurrierende Pläne | genau ein gültiges Architekturdokument (R4); `CHANGELOG.md` als Pflicht |
| X8 | Hexagon und Geräte werden vorgezogen | mittel | unumkehrbare Wirkung ohne Nachweiskette | Tor 4 ist Vorbedingung, schriftlich |

### 10.3 Offene Fragen, die nur am Code oder vom Entwickler zu klären sind

1. Braucht die Weboberfläche die bidirektionale Verbindung, oder genügt SSE? Entscheidet, ob R5 Funktionsverlust bedeutet.
2. Existiert bereits ein Halt-Schalter, eine Ausgangsliste oder eine Kostenzuordnung? In den Architekturdokumenten nicht belegt, im Repo nicht gefunden — aber meine Suche war musterbasiert, nicht vollständig.
3. Welche Schemaversionen tragen die neun Datenbanken heute? Bestimmt den Umfang von R8.
4. Welche der 105 Skills sind tatsächlich im Einsatz? Entscheidet, wie viel von R9 Umbau und wie viel Löschen ist.
5. Ist `a2a_pilot.py` eine aufgegebene Erkundung oder ein Vorhaben? Entscheidet R6.
6. Wurde Code aus dem in ARCHITEKTUR.md beschriebenen Stand übernommen, und wenn ja, welcher? Unbelegt.
7. Welche Modellanbieter sollen unterstützt sein? Bestimmt F6.
8. Soll BotBoy verbreitet werden? Entscheidet V1 und damit, ob die Lizenzlücke ein Problem ist.

### 10.4 Was als Nächstes gemessen werden muss

| Nr | Messung | Zweck | Aufwand |
|---|---|---|---|
| M1 | Zweigabdeckung statt nur Anweisungen für `kernel/` | 64 % Anweisungen sagen nichts über Pfade | S |
| M2 | Mutationsprüfung der Tests für Entscheidung, Freigabe, Nachweis | prüft die Tests, nicht den Code | M |
| M3 | Suite unter Windows mit Python 3.10 lokal | die Zielplattform ist Windows | S |
| M4 | Vollständige Historie auf Geheimnisse | das Repo ist öffentlich | S |
| M5 | Vollaufzählung der Effektsequenzen gegen einen bösartigen Doppelgänger | belegt oder widerlegt die Policy | M |
| M6 | Goldset für das Gedächtnis mit Recall-Messung | ohne das ist jeder Gedächtnisumbau Glaubenssache | M |
| M7 | Falschablehnungsrate vor und nach F1 | X4 früh erkennen | S |
| M8 | Dauer der Kettenprüfung bei realistischer Protokollgröße | Tor der Phase 4 | S |

### 10.5 Bewertung der eigenen Grundlage

**Hoch belastbar:** alles in Kapitel 2 — eigene Messung am Commit `5eaec09`. Reproduzierbar mit den Verfahren aus 1.1.

**Mittel belastbar:** die acht Architekturentscheidungen. Sie folgen aus den Prinzipien des Zielkonzepts und der Recherche vom 17.10.2026, aber keine ist an diesem Code erprobt. A1 trägt das größte Einzelrisiko, weil ein Löschen geprüfter Module davon abhängt, dass die Portierung vollständig ist.

**Niedrig belastbar:** alle Aufwands- und Zeitangaben, die Zeilenschätzung in 7.1, und alle Schwellenwerte in 8.2. Letztere sind nach dem ersten Durchlauf neu zu setzen, nicht zu verteidigen.

**Nicht geprüft:** das Verhalten unter Windows, die vollständige Git-Historie, ob die neun Datenbanken je eigene Schemaversionen tragen, ob der Standardbibliotheks-Pfad funktional gleichwertig zum FastAPI-Pfad ist, und ob die 395 grünen Tests Verhalten prüfen oder vor allem Struktur.
