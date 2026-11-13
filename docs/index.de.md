---
title: "Issue-Assistent: KI-Triage von Issues für GitHub, die nur liest"
description: Kostenlose GitHub Action für KI-Triage von Issues, die nur liest. Die erste Analyse jedes neuen Issues wird vor dem Posten geprüft, mit Labels und Erinnerungen.
social:
  cards_layout_options:
    title: Issue-Assistent
hide:
  - navigation
  - toc
---

<a id="issue-assistant"></a>

# Issue-Assistent

**Sichere KI-Triage von Issues für GitHub.** Eine KI, die nur liest, schreibt eine erste Analyse jedes neuen Issues, und ein Job ohne KI prüft sie, bevor irgendetwas gepostet wird. Labels, Duplikate, Erinnerungen und das Schließen behobener Issues mit dem Release gibt es dazu.

[Loslegen](getting-started.md){ .md-button .md-button--primary }
[Live ansehen](https://github.com/Dennis-Otto/homedarts-for-autodarts/issues/521#issuecomment-6547309975){ .md-button }
[Neuestes Release](https://github.com/Dennis-Otto/issue-assistant/releases/latest){ .md-button }

<picture>
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px) and (prefers-color-scheme: dark)" srcset="images/hero-mobile-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px)" srcset="images/hero-mobile-still-light.webp">
  <source media="(prefers-reduced-motion: reduce) and (prefers-color-scheme: dark)" srcset="images/hero-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce)" srcset="images/hero-still-light.webp">
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="images/hero-mobile-dark.webp">
  <source media="(max-width: 600px)" srcset="images/hero-mobile-light.webp">
  <source media="(prefers-color-scheme: dark)" srcset="images/hero-dark.webp">
  <img src="images/hero-light.webp" alt="Animation: das Leben eines Issues in einem Demo-Projekt. Eine Person meldet ein Issue mit dem Fehlerformular; der Job event setzt die Labels area: dashboard und needs-triage; der Issue-Assistent postet eine erste Analyse mit der wahrscheinlichen Ursache im Code und den noch fehlenden Angaben und setzt connectivity und needs-info; die meldende Person antwortet, und needs-info verschwindet; ein gemergter Pull Request markiert das Issue mit fixed-in-next-release; das Release v1.4.0 schließt es mit einem Link zum Release">
</picture>

<a id="what-it-does"></a>

## Was er tut

<div class="grid cards" markdown>

- :material-robot-outline:{ .lg .middle } **Eine erste Analyse jedes neuen Issues**

    ---

    Eine KI, die nur liest, schreibt eine Zusammenfassung, die wahrscheinliche Ursache mit Links zum betroffenen Code und zur Dokumentation, was die meldende Person versuchen kann und was noch fehlt, in der Sprache der meldenden Person.

- :material-label-multiple-outline:{ .lg .middle } **Labels**

    ---

    Labels aus den Issue-Formularen und aus der Analyse, jedes mit seinem Grund. Die Labels selbst sind Code, in `.github/labels.toml`.

- :material-content-duplicate:{ .lg .middle } **Duplikate**

    ---

    Ein sicheres Duplikat eines Issues, das ein Maintainer geöffnet oder gesichtet hat, bekommt einen Hinweis und schließt drei Tage später, verlinkt mit dem Original, sofern niemand widerspricht. Andere werden zu Vorschlägen für den Maintainer.

- :material-clock-alert-outline:{ .lg .middle } **Offene Fragen**

    ---

    Offene Fragen markieren ein Issue mit `needs-info`. Nach 15 Tagen bekommt die meldende Person eine Erinnerung, die den Tag nennt, an dem das Issue schließt, und nach 30 Tagen schließt es an diesem Tag. Eine Antwort, als Kommentar oder durch eine Bearbeitung, beendet das Warten und öffnet ein Issue wieder, das wegen einer fehlenden Antwort geschlossen wurde.

- :material-tag-check-outline:{ .lg .middle } **Vom Fix zum Release**

    ---

    Ein gemergter Pull Request mit `Fixes #12` markiert das Issue mit `fixed-in-next-release`. Das Issue schließt mit einem Link zum Release, das den Fix ausliefert, und öffnet sich wieder, wenn das Problem bleibt.

- :material-slash-forward-box:{ .lg .middle } **Slash-Befehle**

    ---

    Ein Kommentar wie `/assistant again`, `/assistant ask` mit einer Frage, `/assistant duplicate #12` oder `/assistant mute` steuert den Assistenten auf seinem Issue. Maintainer geben jeden Befehl; die meldende Person kann einmal um eine neue Analyse bitten.

- :material-forum-outline:{ .lg .middle } **Eine erste Antwort in den Discussions**

    ---

    Wo `config.toml` Kategorien nennt, etwa `Q&A`, bekommt eine neue Frage dort eine Antwort derselben KI mit denselben Prüfungen, nie als Antwort markiert; ein Fehler oder ein Funktionswunsch bekommt den Link zu einem Issue-Formular.

- :material-shield-search:{ .lg .middle } **Befunde des Code-Scannings**

    ---

    Die Befunde, die `.github/findings.toml` akzeptiert, werden mit ihrem Grund verworfen, auch in einem Pull Request, wo ihre Review-Threads den Merge blockieren würden. Jeder andere offene Befund lässt einen Lauf scheitern, den ersten nach seinem Auftauchen, damit GitHub einmal darüber mailt, ohne öffentliches Issue.

</div>

Der Maintainer liest jedes Issue und hat das letzte Wort.

**Im Einsatz bei** [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Player Switch for Autodarts](https://github.com/Dennis-Otto/player-switch-for-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync), dem [Repository-Blueprint](https://github.com/Dennis-Otto/repo-blueprint), der Website [dennis-otto.github.io](https://github.com/Dennis-Otto/dennis-otto.github.io) und dem Issue-Assistenten selbst. **Live ansehen:** erste Analysen [eines Fehlers](https://github.com/Dennis-Otto/homedarts-for-autodarts/issues/521#issuecomment-6547309975), bis in den Code verfolgt, [eines Funktionswunsches](https://github.com/Dennis-Otto/paperless-sync/issues/84#issuecomment-6125470710) und [einer im Chinesisch des Meldenden](https://github.com/Dennis-Otto/paperless-sync/issues/345#issuecomment-6582461804), mit Zusammenfassung für den Maintainer, gepostet 2 min 32 s, 2 min 39 s und 3 min 7 s nach dem Öffnen des Issues.

<a id="careful-by-design"></a>

## Vorsichtig gebaut

Was der Assistent von sich aus tut, damit eine Antwort die Lage nie verschlimmert:

<div class="grid cards" markdown>

- :material-cancel:{ .lg .middle } **Spam bekommt keine Antwort**

    ---

    Ein Issue, das die KI für Werbung oder Unsinn hält, bekommt das Label `invalid` und keinen Kommentar, damit sein Autor keine Antwort bekommt. Schließen oder Löschen bleibt beim Maintainer.

- :material-key-alert-outline:{ .lg .middle } **Secrets und persönliche Daten**

    ---

    Ein Token, ein Schlüssel oder ein Passwort in einem Issue bekommt eine Warnung, es zu entfernen, auch aus dem Bearbeitungsverlauf, und es zu ersetzen. Persönliche Daten, etwa eine E-Mail-Adresse, bekommen einen Hinweis, wie man sie entfernt. Die KI wiederholt beides nie.

- :material-shield-bug-outline:{ .lg .middle } **Sicherheitslücken bleiben privat**

    ---

    Eine mögliche Meldung einer Sicherheitslücke bekommt nur einen Verweis auf die private Meldung der Sicherheitsrichtlinie: keine Analyse, keine Fragen, keine Duplikate und einen festen Grund für ihre Labels.

- :material-account-tie-outline:{ .lg .middle } **Das Issue eines Maintainers bekommt nur Labels**

    ---

    Issues, die der Besitzer, ein Mitglied oder ein Collaborator öffnet, bekommen die Labels ihres Formulars, aber keine Analyse. Ein Lauf von Hand gibt trotzdem eine.

- :material-forum-outline:{ .lg .middle } **Das Gespräch bleibt beim Maintainer**

    ---

    Wenn die meldende Person seine Fragen beantwortet, fragt der Assistent höchstens zweimal nach, und nie, sobald der Maintainer sich am Gespräch beteiligt hat.

- :material-lock-outline:{ .lg .middle } **Nichts doppelt, nichts auf geschlossenen Issues**

    ---

    Eine Analyse wird nicht auf einem Issue gepostet, das inzwischen geschlossen oder gesperrt wurde, und eine Aufgabe, die ihren Kommentar schon hat, bekommt keinen zweiten, es sei denn, ein Maintainer [startet sie von Hand](features/runs-by-hand.md) mit dem Override.

</div>

<a id="quick-start"></a>

## Schnellstart

--8<-- "docs/getting-started.de.md:quick-start"

[Loslegen](getting-started.md) hat die [Voraussetzungen](getting-started.md#requirements) eines Repositorys, jeden Schritt im Detail und wie du prüfst, dass er läuft; [Configuration](configuration.md) hat die Einstellungen. Die Action ist auch [im GitHub Marketplace gelistet](https://github.com/marketplace/actions/read-only-issue-assistant), wo ihr Schritt allein `check` ausführt: Die Workflows kommen von `install` oben.

<a id="learn-more"></a>

## Mehr erfahren

<div class="grid cards" markdown>

- :material-rocket-launch-outline:{ .lg .middle } **Loslegen**

    ---

    Was ein Repository braucht, die Einrichtung Schritt für Schritt und wie du prüfst, dass er läuft, bevor die erste Meldung kommt.

    [:octicons-arrow-right-24: Loslegen](getting-started.md)

- :material-star-outline:{ .lg .middle } **Funktionen**

    ---

    Eine Seite für alles, was er tut, von der ersten Analyse eines Issues bis zum Release, das seinen Fix ausliefert, jede mit einem Bild.

    [:octicons-arrow-right-24: Funktionen](features/README.md)

- :material-sitemap-outline:{ .lg .middle } **Wie er funktioniert**

    ---

    Die vier Workflows, die Aufgabe, die jedes Ereignis der KI gibt, und was die KI im Zaum hält.

    [:octicons-arrow-right-24: How it works](how-it-works.md)

- :material-tune-variant:{ .lg .middle } **Konfiguration und Referenz**

    ---

    Die Labels, die Einstellungen und die akzeptierten Befunde mit [jedem Schlüssel](every-key.md), die Befehle, jede Eingabe und Ausgabe der Action und die [Engines](engines.md).

    [:octicons-arrow-right-24: Configuration](configuration.md) · [Reference](reference.md)

- :material-comment-text-multiple-outline:{ .lg .middle } **Was Meldende sehen**

    ---

    Jeder Kommentar, den der Assistent postet, von der ersten Analyse bis zum Release, das den Fix ausliefert, auf Englisch und auf Deutsch.

    [:octicons-arrow-right-24: Was Meldende sehen](comments.md)

- :material-frequently-asked-questions:{ .lg .middle } **Fragen und Fehlerbehebung**

    ---

    Was er kostet, welche Daten GitHub verlassen und was zu tun ist, wenn ein Issue keinen Kommentar bekommt.

    [:octicons-arrow-right-24: FAQ](faq.md)

- :material-compare-horizontal:{ .lg .middle } **Im Vergleich mit anderen Werkzeugen**

    ---

    Die Beispiele der Actions von Claude Code und Gemini CLI, GitHubs AI assessment labeler, GitHub Agentic Workflows, copilot-triage, Copilots Automatisierungen für die Triage, actions/stale, Dosu und GitHubs Erkennung von Duplikaten, Zelle für Zelle mit Quellen.

    [:octicons-arrow-right-24: Comparison](comparison.md)

- :material-sitemap-outline:{ .lg .middle } **Architektur**

    ---

    Die Action, ihre Befehle und die vier Workflows, die sie in einem Repository aufrufen.

    [:octicons-arrow-right-24: Architecture](architecture.md)

- :material-shield-lock-outline:{ .lg .middle } **Sicherheitsdesign**

    ---

    Was der Assistent schützt, wem er vertraut, die Bedrohungen mit ihren Gegenmaßnahmen und die Risiken, die bleiben.

    [:octicons-arrow-right-24: Security design](security.md)

- :material-map-marker-path:{ .lg .middle } **Roadmap und Entscheidungen**

    ---

    Was der Assistent in den nächsten zwölf Monaten vorhat und was er nicht tun wird, und die Entscheidungen, die ihn prägen, jede mit ihren Gründen.

    [:octicons-arrow-right-24: Roadmap](roadmap.md) · [Decisions](decisions/README.md)

- :material-history:{ .lg .middle } **Releases und Changelog**

    ---

    Jedes Release mit seinen Notes auf GitHub und die Änderungen jeder Version.

    [:octicons-arrow-right-24: Releases](https://github.com/Dennis-Otto/issue-assistant/releases) · [Changelog](changelog.md)

</div>

Die Seiten der Anleitung, der Referenz, der Funktionen und der Sicherheit gibt es auf Englisch; diese Seite, [Loslegen](getting-started.md), die [FAQ](faq.md) und [Was Meldende sehen](comments.md) auch auf Deutsch.
