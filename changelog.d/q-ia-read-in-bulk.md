### Performance

- Long issues full of HTML are read up to three times as fast, and never again in a time that grows with the square of their length. A text such as `<!-- <a> --><b c='-->'>` repeated up to GitHub's limit of 65,536 characters took more than a second to read for the AI, and finding the attached files of some texts full of special HTML markup, such as processing instructions (`<?…?>`) or sections of CDATA, took up to 18 seconds; both now take a few hundredths of a second. What the AI reads and which attachments count stays exactly as before.
