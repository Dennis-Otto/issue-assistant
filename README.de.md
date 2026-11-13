<!-- The German README, the twin of README.md: the same headings, pictures and commands,
in German, which tests/test_docs.py compares with those of README.md. Its links lead to
the German pages where there are any, and to the English ones otherwise. -->
# Issue-Assistent

**Sichere KI-Triage von Issues für GitHub.** Eine KI, die nur liest, schreibt eine erste Analyse jedes neuen Issues, und ein Job ohne KI prüft sie, bevor irgendetwas gepostet wird.

[![Dokumentation](https://img.shields.io/badge/docs-website-blue)](https://dennis-otto.github.io/issue-assistant/de/)
[![GitHub Marketplace](https://img.shields.io/badge/marketplace-read--only%20issue%20assistant-purple?logo=github)](https://github.com/marketplace/actions/read-only-issue-assistant)
[![CI](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml)
[![Secret scan](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Dennis-Otto/issue-assistant/badge)](https://scorecard.dev/viewer/?uri=github.com/Dennis-Otto/issue-assistant)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15278/badge)](https://www.bestpractices.dev/projects/15278)
[![REUSE](https://api.reuse.software/badge/github.com/Dennis-Otto/issue-assistant)](https://api.reuse.software/info/github.com/Dennis-Otto/issue-assistant)
[![Sponsor](https://img.shields.io/badge/sponsor-%E2%99%A5-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/Dennis-Otto)

[English version](https://github.com/Dennis-Otto/issue-assistant/blob/main/README.md)

- **Prompt-Injection kann nicht schreiben.** Die KI läuft mit Werkzeugen, die nur lesen, hinter einer Firewall und mit einem Token, das nur liest, oder ganz ohne. Ihre Antwort erreicht GitHub nur über einen Job ohne KI, der sie gegen ein Schema, deine Labels und deine Issues prüft.
- **Antworten, die sich zu lesen lohnen.** Die wahrscheinliche Ursache mit Links zu den Zeilen im Code, was die meldende Person versuchen kann, die noch fehlenden Angaben, Duplikate und Labels, in der Sprache der meldenden Person.
- **Das ganze Leben eines Issues.** Erinnerungen und Schließen für unbeantwortete Issues, Duplikate, die nach drei Tagen schließen, und behobene Issues, die mit dem Release des Fixes schließen, alles ohne KI.

Im Wurzelordner deines Repositorys installieren diese Befehle die Workflows des neuesten Releases, jedes Mal aus einer frischen Kopie, und richten auf GitHub die Umgebung für das Secret der KI ein:

```sh
tag="$(gh release view --repo Dennis-Otto/issue-assistant --json tagName --jq .tagName)"
dir="$(mktemp -d)"
git clone --quiet --depth 1 --branch "$tag" https://github.com/Dennis-Otto/issue-assistant "$dir" &&
  PYTHONPATH="$dir" python3 -P -m issue_assistant install --setup
```

Dann beschreibst du das Projekt, leerst eine Einstellung und probierst es an einem Issue aus, wie [Ein Repository einrichten](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.de.md#set-up-a-repository) zeigt. Die [Voraussetzungen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.de.md#requirements) listen, was er braucht, und die [FAQ](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/faq.de.md#what-does-it-cost), was er kostet.

Du kommst aus dem [GitHub Marketplace](https://github.com/marketplace/actions/read-only-issue-assistant)? Sein *Use latest version* bietet einen nackten Schritt mit leerem `command` an; für sich allein gib ihm `command: check`, wie in [Dauerhaft prüfen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.de.md#keep-it-checked); `install` oben schreibt die Workflows, die den Assistenten ausführen.

**Live ansehen:** erste Analysen [eines Fehlers](https://github.com/Dennis-Otto/homedarts-for-autodarts/issues/521#issuecomment-6547309975), bis in den Code verfolgt, [eines Funktionswunsches](https://github.com/Dennis-Otto/paperless-sync/issues/84#issuecomment-6125470710) und [einer im Chinesisch des Meldenden](https://github.com/Dennis-Otto/paperless-sync/issues/345#issuecomment-6582461804), mit Zusammenfassung für den Maintainer, gepostet 2 min 32 s, 2 min 39 s und 3 min 7 s nach dem Öffnen des Issues.

<picture>
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-still-light.webp">
  <source media="(prefers-reduced-motion: reduce) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-still-light.webp">
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-dark.webp">
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-light.webp">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-light.webp" alt="Animation: das Leben eines Issues in einem Demo-Projekt. Eine Person meldet ein Issue mit dem Fehlerformular; der Job event setzt die Labels area: dashboard und needs-triage; der Issue-Assistent postet eine erste Analyse mit der wahrscheinlichen Ursache im Code und den noch fehlenden Angaben und setzt connectivity und needs-info; die meldende Person antwortet, und needs-info verschwindet; ein gemergter Pull Request markiert das Issue mit fixed-in-next-release; das Release v1.4.0 schließt es mit einem Link zum Release">
</picture>

## Was er tut

Eine GitHub Action, die sich um die Issues eines Repositorys kümmert. Jede Zeile führt zu einer Seite mit einem Bild und Einzelheiten, auf Englisch:

- **[Die erste Analyse](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/first-analysis.md):** die wahrscheinliche Ursache mit Links zum Code, was zu versuchen ist und was fehlt, in der Sprache der meldenden Person.
- **[Das Leben eines Issues](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/life-of-an-issue.md):** Labels sagen, wo ein Issue steht, und der Assistent hält, was seine Kommentare versprechen, ohne KI.
- **[Prioritäten, Zuständige und Issue-Typen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/priorities.md):** Eine Analyse kann eine Priorität setzen, die Zuständigen eines Bereichs zuweisen und der Art mit einem Issue-Typ folgen.
- **[Wartezeiten, Ausnahmen und alte Threads](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/waiting-periods.md):** eine Erinnerung nach 15 Tagen und das Schließen nach 30, oder nach deinen Tagen, mit Ausnahmen und Sperren.
- **[Duplikate](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/duplicates.md):** Ein sicheres Duplikat schließt drei Tage nach seinem Hinweis, sofern niemand widerspricht; andere sind Vorschläge.
- **[Verwandte Repositorys](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/related-repositories.md):** durchsucht auch die Issues von Projekten, die deine Nutzer teilen, und sagt, wenn ein Issue dorthin gehören könnte.
- **[Fragen in den Discussions](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/discussions.md):** eine erste Antwort auf eine neue Frage, mit derselben KI und denselben Prüfungen, nie als Antwort markiert.
- **[Slash-Befehle](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/slash-commands.md):** steuern den Assistenten vom Issue aus, etwa `/assistant again` oder `/assistant ask <question>`.
- **[Läufe von Hand](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/runs-by-hand.md):** starte ihn auf einem Issue mit einer Anweisung, etwa "ask for the logs", oder auf dem Rückstand; nur ein Maintainer lässt ihn erneut antworten.
- **[Vom Fix zum Release](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/fix-to-release.md):** Ein behobenes Issue bleibt offen, bis ein Release den Fix ausliefert, und schließt dann mit einem Link darauf.
- **[Was die KI liest](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/what-the-ai-reads.md):** Jede Aufgabe liest, was sie braucht, nicht mehr, und eine erste Analyse lernt aus den Korrekturen der Maintainer.
- **[Nein zur KI sagen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/opt-out.md):** Ein Kästchen im Issue-Formular hält die KI aus einem Issue heraus, und alles, was ohne KI läuft, geht weiter.
- **[Sprachen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/languages.md):** vierzehn Kataloge für die Meldenden und eine eingeklappte Zusammenfassung für Maintainer, die deren Sprache nicht lesen.
- **[Grenzen der KI-Läufe](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/limits.md):** Kein Fremder kann die Rechnung in die Höhe treiben: Die Läufe, die andere als die Maintainer starten, sind begrenzt.
- **[Was ein Lauf kosten darf](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/cost.md):** Jeder Lauf hält bei seinen Runden und seinen Dollars an, und die Zusammenfassung seines Jobs sagt, was er gekostet hat.
- **[Was der Durchlauf den Maintainern sagt](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/digest.md):** jeden Morgen, was auf wen wartet, wenn du willst in einem angehefteten Issue, und einmal im Monat, wie gut er war.
- **[Kandidaten für Good First Issues](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/good-first-issues.md):** Die Übersicht nennt die Issues, die jemand Neues übernehmen könnte; nur ein Maintainer setzt das Label.
- **[Wenn GitHub ausfällt](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/github-failures.md):** Ein Rate Limit, ein Serverfehler oder eine Störung ändert nie, was der Assistent postet.
- **[Labels als Code](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md#githublabelstoml):** die Labels des Repositorys in `.github/labels.toml`, aus den Issue-Formularen und aus der Analyse, jedes mit seinem Grund.
- **[Befunde des Code-Scannings](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md#findings):** Was `.github/findings.toml` akzeptiert, wird mit seinem Grund verworfen, auch in einem Pull Request; jeder andere offene Befund lässt einen Lauf scheitern, damit GitHub einmal darüber mailt, ohne öffentliches Issue.

Der Maintainer liest jedes Issue und hat das letzte Wort. Eine erste Analyse, auf einem Issue eines Demo-Projekts:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-light.webp" alt="Eine erste Analyse des Issue-Assistenten auf einem Issue eines Demo-Projekts: Dank an die meldende Person, eine Zusammenfassung, die erste Analyse mit der wahrscheinlichen Ursache, was einen Versuch wert ist, die benötigten Angaben mit der Erinnerung nach 15 Tagen, ein verwandtes Issue, Links zur Zeile im Code und zum Abschnitt der Anleitung zur Fehlerbehebung, und eine Fußzeile, die Claude und das gesetzte Label nennt, verlinkt, wie der Assistent arbeitet, um ein Daumen hoch oder runter bittet und das Projekt verlinkt">
</picture>

**Im Einsatz bei** [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Player Switch for Autodarts](https://github.com/Dennis-Otto/player-switch-for-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync), [Repo Pager](https://github.com/Dennis-Otto/repo-pager), dem [Repository-Blueprint](https://github.com/Dennis-Otto/repo-blueprint), [dennis-otto.github.io](https://github.com/Dennis-Otto/dennis-otto.github.io) und dem Assistenten selbst.

## Wie er funktioniert

Vier Workflows laufen im Repository, ein fünfter für Discussions, wo `answer_categories` Kategorien nennt, jeder pinnt ein Release des Issue-Assistenten. Nur der violette Job führt eine KI aus, und er kann nicht schreiben. [How it works](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/how-it-works.md) zeigt, was jeden von ihnen startet und welche Aufgabe jedes Ereignis der KI gibt.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/diagrams/the-four-workflows-of-a-repository-and-what-starts-them.de.dark.svg">
  <img src="docs/images/diagrams/the-four-workflows-of-a-repository-and-what-starts-them.de.svg" alt="Die vier Workflows eines Repositorys und was sie startet: Vier Workflows, jeder mit dem, was ihn startet. In issue-assistant.yml startet ein neues Issue, eine Änderung oder ein Kommentar event, das die Labels des Formulars setzt und die nächste Aufgabe wählt; analyze lässt die KI den Checkout lesen und mit JSON antworten; apply prüft die Antwort und ist der einzige Job, der schreibt. In issue-lifecycle.yml starten jeder Morgen, ein gemergter Pull Request, ein Release und das Ende eines Release-Laufs sweep, das erinnert, Issues schließt, Duplikate schließt und behobene Issues markiert. In labels.yml startet eine Änderung von labels.toml auf main sync-labels, das die Labels anlegt und aktualisiert. In findings.yml starten jeder Morgen, ein Scan auf main und eine Änderung von findings.toml findings, das die akzeptierten Befunde verwirft und an den anderen scheitert.">
</picture>

## Sicherheit

Issues kommen von jedem, also nimmt das Design an, dass ein Issue versucht, die KI zu steuern. Es folgt jeder Gegenmaßnahme, die die Forschung zu [PromptPwnd und Comment and Control](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/security/prompt-attacks.md) für einen Workflow wie ihn empfiehlt:

- **Die KI liest nur.** Sie läuft mit einem Token, das nur liest, und Werkzeugen, die nur lesen, auf einem Runner, dessen Firewall sie nur die Hosts ihrer Engine erreichen lässt.
- **Nichts wird ungeprüft gepostet.** Ein Job, der keine KI ausführt und ihr Secret nie sieht, prüft die Antwort gegen ein Schema, deine Labels und deine Issues.
- **Der Text eines Issues kann sich nicht als jemand anderes ausgeben.** Jeder Text steht in einem Code-Block, den nichts darin schließen kann, unter Überschriften, die nur der Assistent schreibt.
- **Kein Fremder kann die Rechnung in die Höhe treiben.** Personen, die das Repository nicht pflegen, starten standardmäßig höchstens 20 Läufe der KI am Tag, jeden mit [seinen Grenzen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/limits.md).
- **Das Secret bleibt an einem Ort:** in der Umgebung `issue-assistant`, die nur der Standard-Branch nutzen darf, und kein Code eines Pull Requests läuft.

[What keeps it safe](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/how-it-works.md#security) hat alles davon, und das [Sicherheitsdesign](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/security.md) die Bedrohungen, ihre Gegenmaßnahmen und die Tests, die sie festhalten. Melde Sicherheitslücken privat, wie in [SECURITY.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md) beschrieben.

## Im Vergleich mit anderen Werkzeugen

Andere Werkzeuge decken Teile davon ab. GitHub Agentic Workflows und die darauf gebauten Workflows teilen sein Design, und copilot-triage und die Beispiele der Gemini CLI Action für die Triage teilen seine Trennung zwischen einer KI, die liest, und Code ohne KI, der schreibt. Geprüft am 08.11.2026:

- **Die Beispiele der [Claude Code Action](https://github.com/anthropics/claude-code-action/tree/main/examples) für Issues** lassen die KI selbst Labels setzen und kommentieren, mit einem Token, das in Issues schreibt, und ohne Prüfung ihrer Antwort.
- **Die [Beispiele der Gemini CLI Action für die Triage](https://github.com/google-github-actions/run-gemini-cli/tree/main/examples/workflows/issue-triage)** geben Gemini ein Token, das nur liest, und ein eigener Job setzt die Labels, die es gewählt hat, falls es sie gibt. Sie vergeben nur Labels: keine Analyse, keine Frage an die meldende Person, keine Duplikate und keine Erinnerungen.
- **[GitHubs AI assessment labeler](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai)** bewertet ein Issue, wenn ein Maintainer ein Label setzt, und postet die Antwort des Modells ungeprüft aus einem Job, der in Issues schreibt. Er lief auf GitHub Models, das GitHub am 30.07.2026 eingestellt hat.
- **[GitHub Agentic Workflows](https://github.github.com/gh-aw/)** (gh-aw) hat das nächstverwandte Design: einen Agenten mit einem Token, das nur liest, hinter einer Firewall, und Schreibzugriffe über begrenzte *safe outputs*, nachdem ein zweiter Durchgang einer KI nach Bedrohungen gesucht hat. Es ist ein allgemeines Framework in Version 0.x, in dem du die Triage selbst schreibst und kompilierst; Erinnerungen, das Schließen von Duplikaten und das Schließen mit dem Release fehlen ihr.
- **[copilot-triage](https://github.com/crmne/copilot-triage)** gibt einem Modell sechs Werkzeuge, die nur lesen, und hält die Tokens aus seiner Umgebung heraus; Ruby prüft seine Entscheidung und postet sie aus demselben Job, der schreiben kann. Es kümmert sich auch um Discussions, Pull Requests und ein Projekt-Board, antwortet in unter 60 Wörtern oder gar nicht und schlägt vor oder schließt, was ein Release behoben hat oder die meldende Person gelöst nennt. Es läuft mit einem Copilot-Kontingent oder deinem eigenen Schlüssel, ohne Firewall, in Version 0.x.
- **[Copilots Automatisierungen](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automations)** führen GitHubs Cloud-Agenten auf einem neuen Issue aus, mit den schreibenden Werkzeugen, die du wählst. Der Agent gibt jedem Label, Typ, Feld, jeder Zuweisung oder Schließung Grund und Zuversicht und hält unsichere für eine Freigabe zurück, die laut GitHub keine Sicherheitskontrolle ist. Sie laufen nur in privaten und internen Repositorys und übergehen standardmäßig Issues von Personen ohne Schreibrecht.
- **[actions/stale](https://github.com/actions/stale)** schließt inaktive Issues ohne KI, standardmäßig 60 und dann 7 Tage nach der letzten Aktivität. Es schreibt keine Analyse und braucht, um nur Issues zu treffen, die auf die meldende Person warten, ein Label wie `needs-info`, das etwas anderes setzt.
- **[Dosu](https://dosu.dev/pricing)** ist heute ein gehosteter Dienst für Dokumentation. Seine Labels und sein Stale-Bot wurden Open-Source-Workflows auf gh-aw mit deiner eigenen Engine.
- **[GitHubs Erkennung von Duplikaten](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/)**, als öffentliche Vorschau, schlägt bis zu drei ähnliche Issues vor, während eine Person schreibt. Sie ergänzt den Assistenten, der auch Duplikate bestehender Issues findet.

Der [Vergleich](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/comparison.md) hat die ganze Tabelle, mit der Quelle jeder Zelle.

## Dokumentation

Die [Website](https://dennis-otto.github.io/issue-assistant/de/) hat all das auf einer Seite für jedes Thema, mit einer Suche; die Startseite, Loslegen, die FAQ und Was Meldende sehen gibt es auf Deutsch, den Rest auf Englisch:

- **[Loslegen](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.de.md):** was ein Repository braucht, jeder Schritt der Einrichtung und wie du prüfst, dass er läuft.
- **[Configuration](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md):** die Labels, die Einstellungen und die akzeptierten Befunde, und [jeder Schlüssel](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/every-key.md) mit seinem Standard.
- **[Reference](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/reference.md):** die Befehle, jede Eingabe und Ausgabe der Action und wie du einen Prompt auf deinem Rechner ausprobierst.
- **[Engines](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/engines.md):** Claude, Codex, Gemini und Copilot im Vergleich und wie du eine wechselst oder ergänzt.
- **[Was Meldende sehen](https://dennis-otto.github.io/issue-assistant/de/comments.html):** jeder Kommentar, den der Assistent postet, auf Englisch und auf Deutsch.
- **[FAQ](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/faq.de.md):** was er kostet, welche Daten GitHub verlassen und was mit Spam, einem Secret oder einer Sicherheitslücke in einem Issue geschieht.
- **[Troubleshooting](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/troubleshooting.md):** warum ein Issue keinen Kommentar bekommen hat, die üblichen Ursachen, jede mit ihrer Abhilfe.
- **[How accurate is it?](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/accuracy.md)** Die Labels, Duplikate und Fragen gegen das, was die Maintainer entschieden haben, und wie du es selbst misst.
- **[Architecture](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/architecture.md):** die Teile des Assistenten und wie sie getestet werden.
- **[Roadmap](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/roadmap.md)** und **[Changelog](https://github.com/Dennis-Otto/issue-assistant/blob/main/CHANGELOG.md):** was als Nächstes kommt und was jedes Release geändert hat.

<sub>💛 Wenn dir der Issue-Assistent nützt, kannst du [seine Entwicklung unterstützen](https://github.com/sponsors/Dennis-Otto).</sub>

## Zeig, dass du ihn nutzt

Die Fußzeile jedes Kommentars der KI verlinkt den Issue-Assistenten bereits, es sei denn, `[transparency] credit = false` in `.github/issue-assistant/config.toml` lässt das weg. Ein Badge in deiner README sagt es den Leuten, bevor sie ein Issue öffnen, und hilft anderen, das Werkzeug zu finden:

[![issues: triaged by issue assistant](https://img.shields.io/badge/issues-triaged%20by%20issue%20assistant-blue)](https://dennis-otto.github.io/issue-assistant/)

```markdown
[![issues: triaged by issue assistant](https://img.shields.io/badge/issues-triaged%20by%20issue%20assistant-blue)](https://dennis-otto.github.io/issue-assistant/)
```

## Unterstützte Runner

Die Workflows, die `install` schreibt, wählen ihre Runner selbst: `ubuntu-24.04` und `ubuntu-24.04-firewall` für die KI. Ein eigener Job mit der Action, etwa für [`check`](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.de.md#keep-it-checked), läuft auf jedem davon; jeder Pull Request hier testet sie auf jedem:

| Runner | Python | Was er ist |
| --- | --- | --- |
| `ubuntu-24.04` | 3.12 | Der Runner der Workflows |
| `ubuntu-26.04` | 3.14 | Was `ubuntu-latest` seit dem 19.10.2026 bedeutet |
| `ubuntu-24.04-arm` | 3.12 | ARM, kostenlos in öffentlichen Repositorys |

Die Action braucht Python 3.12 oder neuer als `python3`, mit `gh` und `git`; `check` und `evaluate` lesen YAML außerdem mit PyYAML oder `yq`. Auf älterem Python, etwa auf `ubuntu-22.04`, bricht sie im ersten Schritt ab und nennt die Abhilfe: den Job auf `ubuntu-24.04` ausführen oder vorher `actions/setup-python` mit `python-version: 3.12` einfügen.

## Entwicklung

Siehe [CONTRIBUTING.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/CONTRIBUTING.md). Das Skript braucht nur die Standardbibliothek von Python, `gh` und `git`. Die Tests prüfen jede GraphQL-Anfrage gegen GitHubs Schema, [Probeläufe](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/architecture/dry-runs.md) spielen ein Issue durch die KI und jeden wiederverwendbaren Workflow durch, wie ein Repository sie aufruft.

## Lizenz

[MIT](https://github.com/Dennis-Otto/issue-assistant/blob/main/LICENSE)
