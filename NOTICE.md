# SciBranch distribution notice

SciBranch's project-owned source code is licensed under the [MIT License](LICENSE).
Third-party materials retain their own rights and terms; the project license does
not relicense them.

This source tree does not include Synopsys Sentaurus executables, simulator license files,
virtual-machine images, research-paper PDFs or research datasets.

The Sentaurus coding skill includes selected R-2020.09 reference manuals under
`skills/sentaurus-tcad-code/references/manuals/`: `sprocess_ug.pdf`,
`sdevice_ug.pdf` and `sentaurus_relnote.pdf` in `R-2020.09/`. The source-release
builder copies the source originals, checks their release-matched byte sizes and
records paths and sizes in `MANIFEST.json`. The source-release check separately
compares packaged manual bytes with the source originals. These integrity checks
do not establish a right to redistribute the manuals. Their third-party terms
and applicable permissions require separate confirmation.

The three Synopsys manuals are not covered by SciBranch's MIT license. Their
presence in this source tree and public availability at
[mae032241/scibranch](https://github.com/mae032241/scibranch) do not establish
redistribution permission. This notice grants no additional rights to those
third-party materials.
