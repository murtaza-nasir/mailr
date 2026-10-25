# Languages

Mailr is available in seven languages: English, German (Deutsch), Spanish (Español), French
(Français), Brazilian Portuguese (Português (Brasil)), Russian (Русский) and Simplified Chinese
(中文（简体）). Speakr has the same seven. All seven are available in the web app, both Android apps and the texts
written by the server (notifications, Activity, error messages, approval cards, invitation mail).

No setup is required. Each person picks a language under **Settings → You → Region →
Language**; see [Settings](../user-guide/settings.md) and, for the phone apps,
[App settings](../android/settings.md#language).

| Where | With a language chosen | With **Automatic** |
|---|---|---|
| Web | that language | the browser's language, when Mailr has it; else English |
| Android apps | that language | the phone's language, or the language picked for the app in the phone's settings (Android 13 and later) |
| Activity, account health, error messages | that language | the language of the browser they are viewed in |
| Notifications, digests, invitation mail | that language | English |

Traditional Chinese (Taiwan, Hong Kong, Macau) is not given Simplified Chinese: those browsers and
phones get English. Any Portuguese is given Brazilian Portuguese.

## Translation method

The translations were drafted with a local language model and checked by machine: every
placeholder, tag and format argument is kept, each language has its plural forms (Russian has four,
Chinese one), and short labels stay short. A sample of each language was then read and corrected
by hand. In each language, one form of address is used throughout: *du* in
German, *tú* in Spanish, *vous* in French, *você* in Portuguese, *вы* in Russian and *您* in
Chinese.

A text that failed the checks three times is shown in English. The words Ambrose, Mailr, Speakr,
Screener and the names of other products stay as they are.

Corrections to wrong or clumsy texts are welcome: see "Fix a translation" below.

## Fix a translation

The texts are in the repository:

| Part | File |
|---|---|
| Web | `frontend/src/locales/<language>.json` |
| Server | `backend/mailr/i18n/<language>.json` |
| Android | `android/<module>/src/main/res/values-<qualifier>/strings*.xml` |

Edit the text in place, keeping every `{{placeholder}}`, `%1$s` and `<tag>`. At the next run of the
translation tool, the edit is kept as a hand-made translation and is never replaced. Approval card
texts on the web and in `strings_server.xml` are copies of the server's: edit them in
`backend/mailr/i18n/<language>.json` and run `tools/i18n_server_texts.py`.

## Translate new texts, or add a language

Texts that are new or changed since the last run are translated with `tools/i18n_translate.py`, on
all three surfaces, with any OpenAI-compatible endpoint. No hosted model is needed; the texts are short UI
strings.

```sh
export MAILR_I18N_ENDPOINT=http://my-llm:8000/v1
tools/i18n_translate.py                  # every language
tools/i18n_translate.py --lang de,fr     # some languages
tools/i18n_translate.py --check          # no model: exit 1 when a text is missing or stale
tools/i18n_translate.py --offline        # no model: fill new texts with English for now
```

`MAILR_I18N_ENDPOINT` is the endpoint's address, required for a run with a model. `MAILR_I18N_MODEL` names the model (the first one the endpoint serves by default), and
`MAILR_I18N_API_KEY` is sent when a key is required at the endpoint. These three are variables of the script, not of the Mailr server, so they are not in `.env`. At most four requests are in flight
(`--concurrency`, 2 by default).

To add a language:

1. Add it to `tools/i18n/glossary.json`: its name, its Android resource qualifier, its plural forms,
   the form of address, and one translation for each product term (Inbox, Archive, Snooze, Label…).
2. Run `tools/i18n_translate.py --lang <tag>`. The web, server and Android files are written, and a
   cache is kept in `tools/i18n/cache/<tag>.json`.
3. List the tag in `android/notes-core/src/main/res/values/i18n.xml` and in both
   `res/xml/locales_config.xml` files (app and notes). No list is needed for the web and the server: their new
   files are found automatically.
4. Have someone who reads the language look at a sample, fix what is wrong in the output files, and
   run the tests (`npx vitest run src/i18n`, `tests/test_i18n.py`, `TranslationsTest` on Android).
