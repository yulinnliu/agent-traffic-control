# Docs site

How a maintainer builds and publishes the project site
<https://yulinnliu.github.io/agent-traffic-control/>. It is built from the
repository's own documents, each read from its one place
(design `docs/design/ATC-0060.md`):

```text
python adapters/python/scripts/docs_site.py check
python adapters/python/scripts/docs_site.py build <output>
```

Both run from the workspace root in the adapter's environment
(`uv run --locked --project adapters/python ...`). `docs_site.py` stages the
documents its `PUBLISHED` table names into a temporary directory: each site
path is the source's file name in lower case with `_` as `-`, a `README.md`
becomes an index, links into published documents point within the site and
links to every other tracked path point at GitHub. It then builds with
Zensical in strict mode from `site/zensical.toml` and `site/overrides/`, writes
`robots.txt`, and checks the built site: every internal link and anchor, a
title, description, canonical URL and each Open Graph tag once per page,
GitHub's heading anchors as heading ids, the sitemap and `robots.txt`. A
published document may not repeat a heading anchor. `check` is the lint
stage's `docs-site` hook; `build` keeps `<output>/site`.

`.github/workflows/pages.yml` runs `build` on each push to `main` that
changes a watched path, and on a manual dispatch, and deploys the result to
GitHub Pages. The site therefore shows only what the Owner pushed.

The Owner's steps, once, after the first integration:

1. Repository Settings → Pages → Build and deployment → Source: GitHub
   Actions.
2. Push `main` (`atc push root`); the workflow publishes the site. If it ran
   before step 1, rerun it from the Actions tab.
3. Google Search Console: add the URL-prefix property
   `https://yulinnliu.github.io/agent-traffic-control/` and choose the HTML
   tag method. Set the tag's `content` value as the repository variable
   `GOOGLE_SITE_VERIFICATION` (Settings → Secrets and variables → Actions →
   Variables), rerun the workflow, since setting a variable publishes
   nothing, and press Verify once it has deployed. Keep the variable: the
   next publication after deleting it carries no tag, and the verification
   lapses.
4. In Search Console, submit `sitemap.xml`.
5. Repository About → Website: the site URL.
