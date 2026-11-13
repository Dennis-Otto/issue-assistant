---
description: Was der Issue-Assistent kostet, welche Daten GitHub verlassen, in welchen Sprachen er antwortet und warum das Issue eines Maintainers keine Analyse bekommt.
---

<a id="faq"></a>

# FAQ

Was der Issue-Assistent kostet, welche Daten GitHub verlassen und was mit Spam, einem Secret oder einer Sicherheitslücke in einem Issue geschieht. Die [Fehlerbehebung](troubleshooting.md) hat die Abhilfe, wenn etwas nicht läuft, [Loslegen](getting-started.md) beschreibt die Einrichtung Schritt für Schritt, und die [Discussions](https://github.com/Dennis-Otto/issue-assistant/discussions) nehmen jede Frage an, die hier keine Antwort findet.

<a id="what-does-it-cost"></a>

## Was kostet er?

Die Action ist kostenlos und Open Source. Ein Repository zahlt für seine Engine und, wenn es privat ist, für die Minuten seiner Runner, die GitHub öffentlichen Repositorys schenkt. Eine Triage, gemessen an einem Funktionswunsch in Paperless Sync am 12.10.2026:

| Messgröße | Wert |
| --- | --- |
| Vom neuen Issue bis zum Kommentar | 2 min 39 s |
| Zeit auf Runnern | 78 s: 5 s *Handle the event*, 65 s *Analyze with the AI engine*, 8 s *Check and post the analysis*; der Rest war das Warten auf einen Firewall-Runner |
| Die KI | Claude Opus 5.5, 15 Runden, 0,20 $ zu den Preisen der API |

Eine Antwort auf den Kommentar eines Maintainers, die nur beurteilt, ob das Issue jetzt auf die meldende Person wartet, brauchte mit Claude Opus 5.5 3 Runden, 4,4 s und 0,08 $. Die Zusammenfassung des Jobs jedes Laufs zeigt, was er gekostet hat, [in einer Zeile](features/cost.md).

Mit dem Token eines Claude-Abos statt eines API-Schlüssels zählt eine Analyse auf die Nutzungslimits des Abos, statt nach Tokens abgerechnet zu werden. Ein kleineres Modell kostet weniger pro Token, und Nachfragen und die anderen Aufgaben lesen weniger als eine erste Analyse:

| Modell | Eingabe, pro Million Tokens | Ausgabe, pro Million Tokens | Für |
| --- | --- | --- | --- |
| Claude Opus 5.5, `claude-opus-5-5` | 4,00 $ | 20,00 $ | den Standard: die sorgfältigsten Analysen |
| Claude Sonnet 5.5, `claude-sonnet-5-5` | 2,00 $ | 10,00 $ | Repositorys mit vielen Issues: der halbe Preis pro Token |
| Claude Haiku 5.5, `claude-haiku-5-5` | 0,10 $ bis 100.000 Tokens pro Prompt, darüber 0,50 $ | 0,50 $ bis 100.000 Tokens pro Prompt, darüber 2,50 $ | die Antworten auf einen Kommentar, zu den geringsten Kosten |

Für ein Repository mit vielen Issues halbiert `model = "claude-sonnet-5-5"` den Preis jedes Tokens, und `light_model` lässt die Antworten auf einen Kommentar, die nur Ja oder Nein sagen, auf einem günstigeren Modell laufen als die Analysen. Das sind die Preise von Anthropics API im Oktober 2026; [ihre Preisseite](https://platform.claude.com/docs/en/about-claude/pricing) hat die heutigen.

Die anderen Engines rechnen auf ihre eigene Art ab. Codex läuft zu den Preisen von OpenAIs API. Gemini kostet nichts im Rahmen der Tageslimits der kostenlosen Stufe der Gemini-API, die zu einem Repository mit wenigen Issues am Tag passen, und seine bezahlte Stufe rechnet nach Tokens ab. Copilot verbraucht die GitHub AI Credits des Copilot-Plans des Kontos, dem sein Token gehört, nach Modell und Tokens ([nutzungsbasierte Abrechnung](https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing)); Premium-Anfragen nur bei einem Jahresabo von Pro oder Pro+ mit der alten Abrechnung. Nur Claude hält bei den Dollars von `max_budget_usd` an; Gemini hält bei seinen Runden an, Codex und Copilot bei der Zeit des Jobs, 20 Minuten.

Was Fremde ausgeben können, hat [Grenzen](features/limits.md): standardmäßig 20 Analysen, Nachfragen und Antworten nach einem Release am Tag, etwa 4 $ zu den Preisen der API. Ein eigenes Secret der Engine für das Repository begrenzt den Rest: ein API-Schlüssel eines Workspaces mit monatlichem Ausgabenlimit statt des Tokens eines Abos, das auch andere Repositorys oder Personen nutzen, damit eine Flut in einem Repository nie die anderen stoppt.

<a id="can-i-change-the-model"></a>

## Kann ich das Modell ändern?

Ja, mit `model` unter `[engine]` in `.github/issue-assistant/config.toml`, etwa `model = "sonnet"` für ein schnelleres und günstigeres. Ohne den Schlüssel läuft der Standard der Engine: `claude-opus-5-5` für Claude, `gpt-6.1-sol` für Codex, `auto` für Gemini und `auto` für Copilot, womit ihre Kommandozeilen ein Modell wählen. Ein Modell ist ein Name seiner Engine, etwa `flash` für Gemini oder `gpt-5.5` für Codex. `light_model` führt die Antworten auf einen Kommentar aus, die nur Ja oder Nein sagen, und `effort` legt fest, wie viel ein Modell nachdenkt; `max_turns` und `max_budget_usd` begrenzen jeden Lauf, wie [Was ein Lauf kosten darf](features/cost.md) zeigt. `check` lehnt einen Namen ab, der der Kommandozeile der Engine Argumente hinzufügen könnte.

<a id="can-i-change-when-issues-are-reminded-closed-or-locked"></a>

## Kann ich ändern, wann Issues erinnert, geschlossen oder gesperrt werden?

Ja, mit `[lifecycle]` in `.github/issue-assistant/config.toml`: die Tage bis zur Erinnerung, zum Schließen und zum Schließen eines Duplikats, das Fenster nach einem Release, die Nachfragen der KI, die Issues, die durch Label, zugewiesene Person oder 👍 ausgenommen sind, und das Sperren lange geschlossener Issues, das standardmäßig aus ist. Die Kommentare nennen die Tage, die du festlegst. Nenne dieselben Tage in den Beschreibungen von `needs-info`, `stale` und `possible-duplicate` in `labels.toml`, oder gar keine, und in deiner `SUPPORT.md`; `check` scheitert an einem Label, das andere Tage nennt, und an einer Zeile des Hinweises in `SUPPORT.md`, die noch wie die von `install` geschriebene lautet, aber andere Tage oder einen abgeschalteten Schritt nennt. Ein neuer Lauf von `install` schreibt die Tage in eine solche Zeile oder nimmt sie heraus; eine Zeile in deinen eigenen Worten bleibt, wie sie ist.

<a id="which-languages-does-it-answer-in"></a>

## In welchen Sprachen antwortet er?

In jeder Sprache. Englisch, Deutsch, Französisch, Spanisch, Portugiesisch, Italienisch, Niederländisch, Polnisch, Türkisch, Russisch, Ukrainisch, Chinesisch, Japanisch und Koreanisch haben einen Katalog mit den eigenen Worten des Assistenten, und in jeder anderen Sprache schreibt die KI ihren Teil in der Sprache der meldenden Person, zwischen Überschriften und einer Fußzeile auf Englisch. Eine weitere Datei in `locales/` fügt eine Sprache hinzu, wie [Languages](features/languages.md) beschreibt.

<a id="can-the-maintainers-read-an-issue-in-a-language-they-dont-know"></a>

## Können die Maintainer ein Issue in einer Sprache lesen, die sie nicht kennen?

Ja. Eine Analyse, eine Nachfrage oder eine erste Antwort in den Discussions in einer anderen Sprache als der der Maintainer klappt für sie eine Zusammenfassung in ihrer Sprache ein, Englisch, sofern `[maintainers] language` in `config.toml` keine andere nennt: was das Issue meldet und das Wesentliche der Antwort, im einen Kommentar, den der Assistent postet. [A summary for the maintainers](features/languages.md) sagt, wann sie kommt.

<a id="does-the-assistant-learn-from-its-mistakes"></a>

## Lernt der Assistent aus seinen Fehlern?

Ja, aus deinen: Korrigiere das Issue, und die nächste Analyse weiß es. Jede erste Analyse liest, was die Maintainer an den Analysen der 30 zuletzt analysierten Issues geändert haben: die Labels, die sie entfernt oder ergänzt haben, die Hinweise auf Duplikate, denen jemand widersprochen hat, und die Analysen mit einem 👎 von jemandem, der pushen darf. Er braucht keinen eigenen Speicher, denn GitHub bewahrt das alles auf, und `[context] max_analyzed = 0` schaltet es ab; [What the AI reads](features/what-the-ai-reads.md) hat die Einzelheiten.

<a id="how-do-i-know-whether-the-assistant-does-well"></a>

## Woran sehe ich, ob der Assistent gute Arbeit leistet?

Öffne am ersten Tag eines Monats die Zusammenfassung des morgendlichen Durchlaufs, oder starte den Durchlauf von Hand: Er zählt die Labels der KI, die die Maintainer behalten haben, und die, die sie ergänzen mussten, die Zeit vom neuen Issue bis zu seiner Analyse, was aus den Hinweisen auf Duplikate, den Fragen an Meldende und den Kandidaten für Good First Issues wurde, wo `labels.toml` sie hat, und die 👍 und 👎 auf den Analysen, für die 30 Tage davor. Jeden Morgen listet er außerdem, was auf die Maintainer, auf Meldende und auf ein Release wartet.

<a id="where-do-i-see-what-waits-on-me"></a>

## Wo sehe ich, was auf mich wartet?

In der Zusammenfassung des morgendlichen Durchlaufs: unter *Actions* im Workflow *Issue lifecycle*, sein letzter Lauf nach Zeitplan. Er listet, was auf die Maintainer, auf Meldende und auf ein Release wartet, mit Nummer und Link. Um das im Tab Issues zu haben, setze `[digest] issue = true` in `.github/issue-assistant/config.toml`: Der Durchlauf [führt es dann in einem Issue](features/digest.md#the-digest-as-an-issue), das du von Hand anheftest und das er jeden Morgen bearbeitet, ohne jemanden zu benachrichtigen.

<a id="can-the-assistant-find-good-first-issues"></a>

## Kann der Assistent Good First Issues finden?

Er findet Kandidaten, und das Label bleibt deins. Wo `.github/labels.toml` Labels der Gruppe `contribution` hat, wie `good first issue` und `help wanted` der Vorlage, beurteilt die erste Analyse auch, ob jemand Neues im Projekt das Issue übernehmen könnte: eine bestätigte, kleine, örtlich begrenzte Änderung, nie ein Sicherheitsproblem, ein Duplikat oder eine offene Frage des Designs. Das ändert keinen Kommentar und kein Label. Die Übersicht des morgendlichen Durchlaufs listet die, die ein Maintainer gesichtet hat, und du setzt das Label bei denen, die du bestätigst. [Good first issue candidates](features/good-first-issues.md) hat die Regeln und sagt, wie du es einschaltest.

<a id="which-data-leaves-github"></a>

## Welche Daten verlassen GitHub?

Der Job *Analyze with the AI engine* (`analyze`) schickt dem Anbieter der Engine, Anthropic für Claude, OpenAI für Codex, Google für Gemini und GitHub für Copilot, was die KI liest: das Issue und seine Kommentare, höchstens 20.000 Zeichen jedes Textes, kurze Auszüge der anderen Issues und der Discussions, die Labels, `.github/issue-assistant/project.md` und die Dateien des Standard-Branches, die die KI öffnet. Die Engine läuft mit abgeschalteter Telemetrie und abgeschalteten Fehlerberichten. Was der Anbieter mit den Daten tun darf, folgt deiner Vereinbarung mit ihm: für Claude den Bedingungen deines API-Kontos oder deines Abos und den Datenschutzeinstellungen dieses Kontos. Google darf, was die kostenlose Stufe der Gemini-API erreicht, zur Verbesserung seiner Produkte nutzen, ein privates Repository ist mit der bezahlten Stufe also besser bedient. Die Jobs ohne KI teilen StepSecurity, dem Hersteller von Harden-Runner, das Repository, den Lauf und die Hosts mit, die ihre Schritte erreichen, wie das [Sicherheitsdesign](security.md#residual-risks) sagt. Die Action hat keine Datenbank und keinen Dienst von sich aus, und der Hinweis in `SUPPORT.md` sagt Meldenden, welche KI ihr Issue liest.

<a id="can-a-reporter-say-no-to-the-ai"></a>

## Kann eine meldende Person Nein zur KI sagen?

Ja, wo das Issue-Formular das Kästchen *Automated analysis* hat, wie die Vorlagen der Formulare von `install`. Ein angekreuztes Kästchen schaltet das Issue beim Öffnen stumm: Kein Lauf der KI startet darauf, und sein Text bleibt aus dem heraus, was die KI für andere Issues liest, während Labels, Erinnerungen und das Schließen mit einem Release weiterlaufen. `/assistant again` lässt die KI später herein, und der Befehl eines Maintainers startet sie weiterhin. [Saying no to the AI](features/opt-out.md) hat die Einzelheiten und sagt, wie du das Kästchen in deine eigenen Formulare aufnimmst.

<a id="does-it-work-in-private-repositories"></a>

## Funktioniert er in privaten Repositorys?

Ja, mit drei Unterschieden. Die Minuten der Runner zählen auf den Plan des Kontos. Harden-Runner sperrt in den Jobs ohne KI jeden Host außer denen von GitHub nur mit einem Abo oder einer Testphase von StepSecurity und lässt sie sonst ungesperrt laufen. Und die KI liest privaten Code, der wie oben den Anbieter der Engine erreicht.

<a id="does-it-answer-questions-in-discussions"></a>

## Beantwortet er Fragen in den Discussions?

Wo `[discussions] answer_categories` in `config.toml` Kategorien nennt, etwa `Q&A`, bekommt jede neue Discussion dort eine erste Antwort, geprüft wie jede Analyse und nie als Antwort markiert, sobald `install` `discussion-assistant.yml` geschrieben hat; siehe [Questions in Discussions](features/discussions.md). Dort fragt er nicht nach: Ab da übernehmen die Maintainer und die Community.

<a id="why-didnt-my-own-issue-get-an-analysis"></a>

## Warum hat mein eigenes Issue keine Analyse bekommen?

Issues, die ein Maintainer öffnet, also der Besitzer, ein Mitglied der Organisation oder ein Collaborator, bekommen die Labels ihres Formulars und `needs-triage`, aber keine Analyse: Der Assistent schreibt für Meldende, die den Code nicht kennen. Für eine Analyse trotzdem schreibe `/assistant again` in einen Kommentar, oder starte den Workflow von Hand unter *Actions → Issue assistant → Run workflow* mit der Nummer des Issues, der Aufgabe `triage` und abgeschaltetem Probelauf.

<a id="what-happens-to-spam"></a>

## Was geschieht mit Spam?

Ein Issue, das die KI für Spam hält, etwa Werbung oder Unsinn, bekommt das Label `invalid` mit dem Grund der KI in seiner Timeline und keinen Kommentar, damit sein Autor keine Antwort bekommt. Schließen oder Löschen bleibt beim Maintainer. Eine Flut von Spam trifft zuerst auf die [Grenzen der KI-Läufe](features/limits.md): Nach 3 Issues einer Person oder 20 Issues und Antworten aller an einem Tag bekommen die Issues nur noch ihre Labels, bis der Maintainer einen Blick darauf wirft.

<a id="what-if-an-issue-shows-a-secret-or-personal-data"></a>

## Was, wenn ein Issue ein Secret oder persönliche Daten zeigt?

Die Analyse beginnt mit einer Warnung. Bei einem Secret, etwa einem Token, einem Schlüssel oder einem Passwort, bittet sie die meldende Person, es aus dem Beitrag und aus dessen Bearbeitungsverlauf zu entfernen und es zu ersetzen, da andere es gesehen haben könnten. Bei persönlichen Daten, etwa einer E-Mail-Adresse, einer öffentlichen IP-Adresse oder der Adresse eines privaten Servers, sagt sie, wie man sie entfernt. Die KI wiederholt solche Daten nie, und eine Antwort mit etwas wie einem Token wird nie gepostet.

<a id="what-if-an-issue-reports-a-vulnerability"></a>

## Was, wenn ein Issue eine Sicherheitslücke meldet?

Der Kommentar bittet die meldende Person nur, sie privat zu melden, wie die Sicherheitsrichtlinie des Repositorys beschreibt, und die Einzelheiten aus dem Issue zu entfernen. Er hat keine Zusammenfassung, keine Analyse, keine Fragen und keine Duplikate, und der Grund für seine Labels in der Timeline ist ein fester Satz statt der Worte der KI. Die [Auswahl](getting-started.md#what-install-writes), die `install` für ein Repository ohne Issue-Formulare schreibt, bietet die private Meldung an, bevor jemand ein Issue öffnet.

<a id="how-do-i-turn-it-off"></a>

## Wie schalte ich ihn ab?

Setze die Repository-Variable `ISSUE_ASSISTANT_AI` auf `off`, zum Beispiel mit `gh variable set ISSUE_ASSISTANT_AI --body off`, und die KI bleibt aus, während Labels, Erinnerungen und Schließen weiterlaufen; das Entfernen des Secrets der Engine bewirkt dasselbe. Für ein einzelnes Issue hält ein Kommentar `/assistant mute` die KI davon ab, dort von selbst zu starten. Um alles abzuschalten, deaktiviere oder lösche die vier Workflows und den der Discussions, `discussion-assistant.yml`, der ebenfalls mit der Engine antwortet; [Den Assistenten entfernen](getting-started.md#remove-the-assistant) nennt, was die Einrichtung sonst im Repository und auf GitHub hinterlassen hat.

<a id="what-if-the-firewall-runner-is-gone"></a>

## Was, wenn der Firewall-Runner wegfällt?

Die KI läuft auf `ubuntu-24.04-firewall`, GitHubs Runner mit der Egress-Firewall, der eine technische Vorschau ist. Sollte GitHub ihn umbenennen oder einstellen oder dein Konto ihn verlieren, wartet der Job *Analyze with the AI engine* (`analyze`) bis zu einem Tag in der Warteschlange, und jeder spätere Lauf desselben Issues wartet dahinter. Nenne einen anderen Runner für die KI in der Repository-Variable `ISSUE_ASSISTANT_AI_RUNNER`, ohne neues Release:

```sh
gh variable set ISSUE_ASSISTANT_AI_RUNNER --body ubuntu-24.04
```

Der Runner muss einer von GitHubs Runnern mit Ubuntu in fester Version sein; der Job `event` lehnt jeden anderen ab, etwa einen selbst gehosteten Runner oder `ubuntu-latest`, bevor die KI läuft. Dort führt der Job *Analyze with the AI engine on the runner in reserve* (`analyze-in-reserve`) die Schritte von `analyze` an dessen Stelle aus, und sein erster Schritt ist Harden-Runner im Sperrmodus mit den Hosts deiner `.github/egress-firewall.yaml`, und bevor die Engine läuft, lässt ein Schritt den Job scheitern, wenn Anfragen an einen Host außerhalb davon nach einer Minute noch durchkommen. Das ist der Kompromiss: Die Firewall sperrt für die ganze Maschine, außer Reichweite jedes Schritts, Harden-Runner für den Job, innerhalb der Maschine; in einem privaten Repository sperrt er nur mit einem Abo von StepSecurity, ohne eins läuft die KI dort also nicht. `gh variable delete ISSUE_ASSISTANT_AI_RUNNER` bringt die KI zurück auf den Firewall-Runner, und [Entscheidung 0019](decisions/0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md) erklärt die Wahl. Der Canary dieses Repositorys scheitert, wenn sein `analyze` länger als 30 Minuten auf den Firewall-Runner wartet, und öffnet sofort ein Issue, damit schnell ein Release mit einem neuen Label folgt.

<a id="why-must-the-workflows-equal-the-templates"></a>

## Warum müssen die Workflows den Vorlagen gleichen?

Die Sicherheit des Assistenten steckt in seinen Workflows: in den Berechtigungen jedes Jobs, dem Runner mit der Firewall und der Reihenfolge der Schritte. Deshalb scheitert `check`, wenn ein Workflow von seiner Vorlage in etwas anderem abweicht als den Commit-Hashes seiner Actions, wie die Entscheidungen [0006](decisions/0006-the-templates-exactly.md) und [0007](decisions/0007-templates-live-in-templates.md) erklären. Die Workflows rufen nur die Jobs eines Releases auf, ein Update verschiebt also ihre Pins und ändert sonst nichts, wie Entscheidung 0015 der [Entscheidungen](https://github.com/Dennis-Otto/issue-assistant/tree/main/docs/decisions) erklärt. Eine Änderung, die ein Repository braucht, ist hier als Issue willkommen und erreicht mit einem Release jedes Repository.

<a id="does-it-need-claude"></a>

## Braucht er Claude?

Nein. Claude ist der Standard, und Codex, Gemini und Copilot sind die anderen Engines, die `name` unter `[engine]` wählt; Gemini startet kostenlos, im Rahmen der Tageslimits der kostenlosen Stufe seiner API. [Engines](engines.md) vergleicht sie und zeigt, wie du die Engine wechselst.
