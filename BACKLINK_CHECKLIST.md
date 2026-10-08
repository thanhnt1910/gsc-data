# Backlink checklist

Rules for vetting a backlink source **before** recommending it, and for
confirming it **after** posting. Written on 2026-10-07 after several
sources that looked dofollow turned out not to pass anything.

## What went wrong (2026-10-07)

| Source | What we checked | What we missed | Result |
|---|---|---|---|
| Bear Blog | An approved, long-running blog (the founder's) | New blogs are manually reviewed and carry `noindex, nofollow` until approved | Post live, link worth nothing until review |
| Write.as | A blog owned by an account | Anonymous posts render our links as `nofollow`; free accounts are closed (Pro only, $9/month) | Skipped after posting |
| WordPress.com | Sample blogs on the platform | A new site stays "Coming Soon" (`noindex, nofollow`) until **Launch site** | Fixed by launching, but only after a check |
| Edublogs | Sample teacher blogs | "Allow search engines" is a paid Pro feature; free blogs are hidden from Google | Skipped |
| Weebly | Sample sites | The sign-up page returns 404 (Weebly moved to Square Online) | Skipped |
| SaaSHub | Nothing before submitting | Product and alternatives pages are ~17% dofollow, so our listing link is almost certainly `nofollow` | Kept for referral traffic only |

The common mistake: **checking a page that is not the kind of page our
link will live on.** A platform's homepage, its staff blog, or an
established user's page can be fully dofollow while a brand-new, free or
anonymous page on the same platform is not.

## Owner preference (2026-10-07)

**No email outreach.** The site owner has decided that cold email (asking
teachers, librarians, bloggers or editors to add a link or accept a guest
post) is not worth the effort. Do not propose sources that need an email
or a contact-form pitch to a person. Only propose sources where the owner
can publish or submit the link themselves: own pages on free platforms,
profiles, and directories or listings with a self-serve submission form.
Sources already contacted (Midwest, HomeEducator, Sphero, ATA Library)
are left to run their course with no follow-ups beyond what was noted.

## Before recommending a source

1. **Search the tracker first, every status.** Look up the domain in the
   tracker's `sites` collection (todo, waiting, live, skip, rejected).
   Never re-suggest a source that is already there unless something new
   changes the decision, and say what changed. On 2026-10-08 eight
   sources (MERLOT, dev.to, OER Commons, Curlie, Pearltrees, Fazier,
   TeachersFirst, BoardGameGeek) were re-suggested as "new" although they
   were already live or skipped, two of them with known problems.
2. **Check the exact page type our link will appear on.** Run
   `check-rel.yml` on a page that matches ours: same account type (free,
   not paid), same age (new, not years old), same format (profile, post,
   listing, directory entry). For directories, check a listing of a
   comparable product, not the directory's homepage.
3. **Check the listed site's own link, not the page total.**
   `dofollow_share` counts every external link on the page, menus and
   footers included. On a listing page, confirm the link to the listed
   site itself is a plain `<a href>` and dofollow. MERLOT samples showed
   15/15 dofollow, but those were MERLOT's own footer links: "Go to
   Material" is a POST form through `/merlot/gom.htm`, so a listing
   passes no link at all.
4. **Look for gating on new or free accounts.** Read the platform's docs
   or FAQ for: manual review or approval queues, "noindex until
   launched/verified", search-engine visibility as a paid feature,
   anonymous vs account posts, link limits for new users.
5. **Confirm the sign-up actually works and is free.** Open the sign-up
   page. A 404, a closed free tier, or a required paid plan means skip.
6. **Read the dofollow share as a signal, not proof.** `good` on a
   sample page only says that page is mostly dofollow. A `dofollow_share`
   under 50%, or `check` / `skip`, means the source is not worth time for
   SEO (it can still be worth it for referral traffic, say so).
7. **Dofollow is not the same as valuable.** A link on a page nobody
   links to passes almost nothing. New subdomains on `github.io`,
   `netlify.app`, `pages.dev`, `neocities.org` and similar hosts are
   separate sites to Google (Public Suffix List) and inherit no authority
   from the platform. They are fine in small numbers, but not as a daily
   task (decided 2026-10-08).
8. **For outreach (asking a page owner to add a link), check the page is
   still maintained.** Look for a recent date, a current school year or
   staff directory listing for the owner. Old Weebly and classic Google
   Sites pages are often abandoned: on 2026-10-07 the first teacher
   email (Mrs. Willis, Morehead Elementary) bounced with "account is
   inactive" because she had left the school.
9. **Say the confidence level out loud.** If a source could not be
   verified (403 / 429, blocked checker, no matching sample page), tell
   the user it is unverified instead of listing it as dofollow.

## After posting

1. **Always run `check-rel.yml` on the public URL** (not the edit or
   dashboard link). Required result:
   - `indexable` = `yes`
   - `page_nofollow` = `no`
   - `links_to_us` lists `dofollow` for our links
2. **If it fails, find out why before moving on** (unlaunched site,
   review queue, anonymous post, wrong URL), fix it or mark the source
   `skip` / `waiting` in the tracker with the reason.
3. **Record the result in the tracker** (status, live URL, date, the
   check-rel numbers). Only `links_to_us` = `dofollow` counts as "live".
4. **Re-check `waiting` sources** (review queues) on the date noted in
   the tracker.
