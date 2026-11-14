# Publishing VeilWave on GitHub

This checklist assumes the repository folder is named `veilwave-repo`.

## 1. Review what will become public

Before publishing, check that you have permission to publish every file and that the repository contains no:

- private speech recordings or identifiable voices;
- medical or personal information;
- API keys, passwords, access tokens, or private SSH keys;
- third-party datasets or model weights without redistribution permission;
- unpublished reviewer correspondence or confidential journal material.

The `.gitignore` excludes common audio and model files, but it is not a substitute for checking the files yourself.

Review the project files:

```bash
git status --short
```

If this is a fresh folder and Git has not been initialized, the command may report that it is not a repository; continue to the next step.

## 2. Create the GitHub repository

1. Sign in to GitHub.
2. Select **New repository**.
3. Repository name: `VeilWave` (or `veilwave`).
4. Add a short description, for example:  
   `Single-microphone audio-processing research prototype associated with the VeilWave manuscript.`
5. Choose **Public** only if you have approval to release the code and all included materials.
6. Do **not** initialise it with a README, `.gitignore`, or licence. This folder already contains those files.
7. Select **Create repository**.

Keep the new repository page open so you can copy its HTTPS URL.

## 3. Initialise Git locally

Open a terminal in the extracted `veilwave-repo` folder:

```bash
git init -b main
git add .
git status --short
```

Carefully inspect the staged file list. If a recording, dataset, model weight, private file, or unrelated document appears, stop and unstage/remove it before proceeding.

To unstage a file:

```bash
git restore --staged path/to/file
```

Then commit:

```bash
git commit -m "Initial public VeilWave research prototype"
```

If Git asks for your identity, configure the name and email you want associated with your commits:

```bash
git config --global user.name "Your Name"
git config --global user.email "YOUR-GITHUB-NOREPLY-EMAIL"
```

Use an email address you are comfortable associating with public commits.

## 4. Connect the local folder to GitHub

Replace the example URL with the HTTPS URL shown on your own new repository page:

```bash
git remote add origin https://github.com/YOUR-USERNAME/VeilWave.git
git remote -v
git push -u origin main
```

Complete GitHub's sign-in flow if requested. Do not put a password or personal access token directly into source files or terminal commands.

## 5. Check the published repository

Refresh the GitHub page and verify:

- `README.md` displays correctly;
- `PAPER_COMPATIBILITY.md` and `HARDWARE_NOTES.md` are present;
- `LICENSE` and `CITATION.cff` are present;
- the `tests/` and `.github/workflows/` folders are present;
- no private recordings, datasets, model weights, or unrelated documents were uploaded.

Open the **Actions** tab and check the CI workflow. It should run after the push. A green workflow means the configured software tests passed on the CI runners; it does not verify hardware, Raspberry Pi performance, speech-quality improvement, or clinical suitability.

## 6. Add repository metadata

On GitHub:

1. Add repository topics such as `audio-processing`, `speech-enhancement`, `signal-processing`, `python`, and `research-prototype`.
2. Add a short website or project page only if you have one.
3. Add a contact method or issue policy if you want people to contact you.
4. Check the repository's About description and visibility.
5. Do not add a release tag until you are comfortable treating the uploaded state as a versioned public release.

## 7. Make future changes

After editing files locally:

```bash
git status
git add README.md veilwave.py tests/
git diff --cached
git commit -m "Describe the change"
git push
```

For broader changes, create a branch and open a pull request so the tests run before merging.

## Suggested first public release wording

> VeilWave is an early-stage, single-microphone audio-processing research prototype. The repository includes experimental Python code and unverified Arduino firmware. It is not a validated hearing aid or medical device, and it does not contain the complete software and audio package required to reproduce the manuscript's pilot evaluation. See the paper-compatibility and hardware notes before using or citing the code.
