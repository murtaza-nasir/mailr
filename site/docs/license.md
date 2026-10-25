# Licence

Copyright © 2026 the Mailr authors.

Mailr is dual-licensed.

## GNU Affero General Public License v3

Mailr is free software. You can redistribute it and modify it under the terms of the
[GNU Affero General Public License, version 3](https://www.gnu.org/licenses/agpl-3.0.html), as
published by the Free Software Foundation. The full text is in the `LICENSE` file of the repository.

In short:

- Mailr may be used for any purpose, at home or at work.
- The source may be studied and changed.
- A changed version that is shared, or offered to people over a network, must come with an offer of
  its source code under the same licence.

## Commercial licence

For users or organisations who cannot or do not wish to comply with the terms of the AGPLv3, a
separate commercial licence is available. Please contact the
**Mailr maintainers** for details on obtaining a commercial licence.

You must choose one of these licences to use, modify or distribute Mailr. If you use or distribute
it without a commercial licence agreement, the terms of the AGPLv3 apply.

## Third-party data

The Docker image includes the fastText language identification model `lid.176.ftz` by Facebook AI
Research, which is used to offer translations. The model is licensed under the
[Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/)
licence; the fastText code is MIT-licensed. Source:
[fastText language identification](https://fasttext.cc/docs/en/language-identification.html)
(A. Joulin, E. Grave, P. Bojanowski, T. Mikolov, *Bag of Tricks for Efficient Text
Classification*, 2016; A. Joulin et al., *FastText.zip: Compressing text classification models*,
2016). The file is downloaded unchanged when the image is built.

The help pages in the image (this guide, at `/help/` on the server) are built with
[MkDocs](https://www.mkdocs.org) (BSD-2-Clause) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) (MIT). They include
[Mermaid](https://mermaid.js.org) (MIT) for diagrams.

The interactive API reference (`/api/v1/docs`) is drawn with
[Swagger UI](https://github.com/swagger-api/swagger-ui) (Apache-2.0), whose two files are
downloaded unchanged when the image is built and served from the server itself.

## Contributing

Contributions are welcome. **By submitting a pull request, you agree to the Contributor License
Agreement in `CLA.md`.** No separate signature is needed. Contributors keep the copyright of their work; under the
agreement, Mailr can offer each contribution under both licences.
