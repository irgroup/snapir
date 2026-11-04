# Responsible use

SnapIR collects data from a search provider and from every website that the search results link to. Each source has its own terms, and the pages can contain copyrighted and personal material. This page describes the safeguards SnapIR has, what remains your responsibility, and what we learned about sharing the data (paper §5). It is not legal advice. Check with your institution's legal and ethics offices.


## Defaults in SnapIR

- **`robots.txt` is obeyed.** It is checked for every URL and every redirect target. Disallowed pages are not loaded and get the status `robots`. The rule group is chosen by `SNAPIR_ROBOTS_USER_AGENT` (default `SnapIR`). 
- **Rate limiting.** At least 3 s between loads per domain, or the site's  `Crawl-delay` if that is longer.
- **No disguise by default.** The browser does not hide that it is automated. `--stealth` changes that, and captures made with it are marked `"stealth": true`.
- **Search results come from [SerpApi](https://serpapi.com)**, under its terms. SnapIR does not scrape search engines directly.

`--ignore-robots` and `--stealth` exist for cases where you have a legal basis to use them, for example a text and data mining exception for research. Document that basis when you use them.

