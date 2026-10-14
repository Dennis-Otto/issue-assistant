## Was mit deinem Issue geschieht

Innerhalb weniger Minuten gibt der Issue-Assistent einem neuen Issue seine Labels und schreibt eine erste Analyse: eine Zusammenfassung, die wahrscheinliche Ursache oder die Dokumentation, die hilft, verwandte Issues und, wenn nötig, Fragen. Der Assistent nutzt {engine}, eine KI von {vendor}; sie liest den Text des Issues und das Repository und kann sich irren. Die Maintainer lesen jedes Issue und entscheiden. Der Assistent ist [ein eigenes Open-Source-Projekt](https://github.com/Dennis-Otto/issue-assistant).

- **Fragen** markieren das Issue als wartend auf dich. Antworte in einem Kommentar oder indem du das Issue bearbeitest. Ohne Antwort folgt nach 15 Tagen eine Erinnerung, nach 30 Tagen wird das Issue geschlossen; eine Antwort öffnet es wieder.
- **Ein wahrscheinliches Duplikat** eines offenen Issues bekommt einen Hinweis und wird 3 Tage später geschlossen, damit das Gespräch an einer Stelle bleibt. Geht es um etwas anderes, schreib einen Kommentar oder reagiere auf den Hinweis mit 👎, dann bleibt es offen.
- **Ein Fix** auf dem Standard-Branch markiert das Issue mit `fixed-in-next-release`. Es bleibt offen, bis ein Release den Fix ausliefert, und wird dann mit einem Link auf das Release geschlossen. Besteht das Problem nach dem Update weiter, schreib innerhalb von 30 Tagen einen Kommentar, und das Issue wird wieder geöffnet.
