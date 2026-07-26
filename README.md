# Musician Difficulty Rating Study

A standalone Flask website for collecting anonymous musician difficulty labels.
It intentionally contains no song-analysis or machine-learning code.

The free deployment uses:

- Render's free web service for the Flask website
- Supabase's free Postgres database for ratings and song metadata
- A private Supabase Storage bucket for the approved audio files

Audio and ratings are not stored on Render or committed to GitHub. The browser
receives only a short-lived signed audio URL, while the Supabase secret key stays
on the server.

## Data collected

- Anonymous participant ID
- Main instrument and years of experience
- Beginner, Intermediate, or Advanced rating
- Confidence from 1 to 5
- Optional non-identifying comment
- UTC submission time

Participants see songs in a balanced order: unrated songs with the fewest total
ratings are presented first. A participant can return with the same ID and
continue without rating the same song twice.

## 1. Create the free Supabase data store

Create a free Supabase project. Open its SQL Editor, paste the contents of
`supabase_schema.sql`, and run it once.

From the project's API settings, copy:

- Project URL (`https://...supabase.co`)
- Secret key (`sb_secret_...`)

Never expose the secret key in a participant link, browser code, screenshot, or
Git commit.

## 2. Configure locally and upload the approved songs

Copy `.env.example` to `.env` and set all private values. Then run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\upload_songs.py "C:\path\to\approved-songs"
```

The uploader creates a private `rating-audio` bucket, uploads each unique MP3 or
WAV, and registers it in the songs table. It can safely be run again: existing
files are skipped by SHA-256 hash.

To test the site locally:

```powershell
.\.venv\Scripts\python.exe app.py
```

Participant invitation:

```text
http://127.0.0.1:5000/?access=YOUR_INVITE_TOKEN
```

Private administration:

```text
http://127.0.0.1:5000/admin?admin_token=YOUR_ADMIN_TOKEN
```

The admin page exports raw ratings and consensus labels. Consensus requires at
least three independent ratings and at least 60% agreement.

## 3. Deploy free on Render

Create a Render Blueprint from this GitHub repository. `render.yaml` selects the
free plan. Enter these private environment values when Render asks for them:

- `INVITE_TOKEN`
- `ADMIN_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

Render generates `SECRET_KEY` automatically. Do not add a disk; Supabase provides
the persistent storage.

The stable participant link is:

```text
https://YOUR-SERVICE.onrender.com/?access=YOUR_INVITE_TOKEN
```

The private admin link is:

```text
https://YOUR-SERVICE.onrender.com/admin?admin_token=YOUR_ADMIN_TOKEN
```

Free services can sleep when inactive, so the first visit may take longer to
open. Free Supabase projects can also pause after extended inactivity; open the
Supabase dashboard to restore the project if that happens.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Research and copyright

Obtain the consent and ethics approval required by the institution. Do not
collect names or contact details. Host only audio that the study is authorized
to distribute to participants.
