### Bug fixes

- An answer that repeats hex as long as a commit after the name of a credential that ends in a number, such as `key1=`, `token2:` or `password12 is`, is now held back as well. A commit alone, in a link or after the word commit is still posted, and so is one after a word such as `keyboard1` or `sha1`.
