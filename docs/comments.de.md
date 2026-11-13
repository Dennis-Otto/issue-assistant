---
description: Jeder Kommentar, den der Issue-Assistent auf einem Issue oder einer Discussion postet, auf Englisch und Deutsch, von der ersten Analyse bis zum Release.
---

<a id="what-reporters-see"></a>

# Was Meldende sehen

Jeder Kommentar, den der Issue-Assistent auf einem Issue postet, und auf einer Discussion, wo er Fragen beantwortet, so wie Meldende ihn sehen, auf Englisch und auf Deutsch, und die erste Analyse und die Erinnerung auch auf Chinesisch, Japanisch und Russisch. Der Assistent schreibt in der Sprache der meldenden Person: Englisch, Deutsch, Französisch, Spanisch, Portugiesisch, Italienisch, Niederländisch, Polnisch, Türkisch, Russisch, Ukrainisch, Chinesisch, Japanisch oder Koreanisch, mit den Texten der Kataloge in `locales/`, und in jeder anderen Sprache mit seinen eigenen Worten auf Englisch um die der KI herum; [Languages](features/languages.md) erzählt, wie er wählt. Ein Kommentar in einer anderen Sprache als der der Maintainer klappt für sie eine Zusammenfassung in ihrer Sprache ein, wie das Beispiel *A summary for the maintainers* zeigt.

Die Website erzeugt die Kommentare bei jedem Build, mit den Funktionen und den Texten des Pakets `issue_assistant` ihrer Version, für das Demo-Projekt der Bilder mit erfundenen Personen. In der ersten Analyse und in der Nachfrage schreibt die KI die Zusammenfassung, die Analyse, was zu versuchen ist, die Fragen, warum ein Link wichtig ist und die Zusammenfassung für die Maintainer, und in einer ersten Antwort auf eine Discussion die Antwort, die Fragen, warum ein Link wichtig ist und die Zusammenfassung für die Maintainer; der Rest ist der eigene Text des Assistenten, in jedem Repository derselbe: auch die Zeile, die mehr als drei Links einklappt, und die Fußzeile, die den Hinweis an Meldende verlinkt, um ein 👍 oder 👎 bittet und das Projekt verlinkt, es sei denn, `[transparency] credit = false` lässt das weg. Ein Issue, das die KI für Spam hält, bekommt keinen Kommentar, nur das Label `invalid`, und die Issues der Maintainer bekommen die Labels ihres Formulars, aber keine Analyse. Die Überschriften und Erklärungen der Beispiele unten sind englisch.

<!-- comments of issue_assistant -->
Die Kommentare selbst stehen auf der Seite Was Meldende sehen in der Anleitung der [Website](https://dennis-otto.github.io/issue-assistant/de/), die sie bei jedem Build aus dem Paket `issue_assistant` erzeugt.
<!-- end of the comments -->
