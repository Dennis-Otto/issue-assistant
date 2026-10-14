## Was mit deinem Issue geschieht

Innerhalb weniger Minuten gibt der Issue-Assistent einem neuen Issue seine Labels und schreibt eine erste Analyse: eine Zusammenfassung, die wahrscheinliche Ursache oder die Dokumentation, die hilft, verwandte Issues und, wenn nötig, Fragen. Der Assistent nutzt {engine}, eine KI von {vendor}; sie liest den Text des Issues und das Repository und kann sich irren. Die Maintainer lesen jedes Issue und entscheiden. Der Assistent ist [ein eigenes Open-Source-Projekt](https://github.com/Dennis-Otto/issue-assistant).

- **Fragen** markieren das Issue als wartend auf dich. Antworte in einem Kommentar oder indem du das Issue bearbeitest. Ohne Antwort folgt nach {remind_days} eine Erinnerung, nach {close_days} wird das Issue geschlossen; eine Antwort öffnet es wieder.
- **Ein wahrscheinliches Duplikat** eines offenen Issues bekommt einen Hinweis und wird nach {grace_days} geschlossen, damit das Gespräch an einer Stelle bleibt. Geht es um etwas anderes, schreib einen Kommentar oder reagiere auf den Hinweis mit 👎, dann bleibt es offen.
- **Ein Fix** auf dem Standard-Branch markiert das Issue mit `fixed-in-next-release`. Es bleibt offen, bis ein Release den Fix ausliefert, und wird dann mit einem Link auf das Release geschlossen. Besteht das Problem nach dem Update weiter, schreib innerhalb von {reopen_days} einen Kommentar, und das Issue wird wieder geöffnet.
- **Alte Issues** werden gesperrt, wenn sie seit mehr als {lock_days} geschlossen sind. Kommt das Problem wieder, eröffne ein neues Issue und verlinke das alte.
