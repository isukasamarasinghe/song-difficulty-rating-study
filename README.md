# Musician Difficulty Rating Study

A standalone Flask website for collecting anonymous musician difficulty labels.
It intentionally contains no song-analysis or machine-learning code.

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

## Local setup

Create `.env` from `.env.example`. Point `AUDIO_DIR` to the approved MP3 folder,
then run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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

## Render deployment

`render.yaml` defines one lightweight Flask web service with a 1 GB persistent
disk mounted at `/var/data`. Set different private values for `INVITE_TOKEN` and
`ADMIN_TOKEN` in Render. Upload approved audio files to `/var/data/audio`; never
commit MP3 files or the SQLite database to Git.

The stable participant link is:

```text
https://YOUR-SERVICE.onrender.com/?access=YOUR_INVITE_TOKEN
```

The private admin link is:

```text
https://YOUR-SERVICE.onrender.com/admin?admin_token=YOUR_ADMIN_TOKEN
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Research and copyright

Obtain the consent and ethics approval required by the institution. Do not
collect names or contact details. Host only audio that the study is authorized
to distribute to participants.
