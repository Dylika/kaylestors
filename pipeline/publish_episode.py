"""Publish an episode written in Claude Code.

    python pipeline/publish_episode.py <draft_dir> <episode_number>

<draft_dir> holds ep<N>.md (grammar-checked text) and ep<N>_meta.yml (description,
image_prompt, summary). Updates the post, the series tracker and image_prompts.md.
When no series is in progress, <draft_dir>/series_plan.yml (title, slug, logline,
total_episodes, next_episode: 1, bible, outline, episodes: []) starts a new one.
"""
import sys, yaml
sys.path.insert(0, "pipeline")
import generate as g
S = sys.argv[1]
n = int(sys.argv[2])
state_path, _ = g.load_series()
if not state_path:
    plan_state = yaml.safe_load(open(f"{S}/series_plan.yml", encoding="utf-8"))
    state_path = g.SERIES_DIR / plan_state["slug"] / "series.yml"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    g.save_series(state_path, plan_state)
state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
assert state["next_episode"] == n, f"tracker says next is {state['next_episode']}, not {n}"
info = yaml.safe_load(open(f"{S}/ep{n}_meta.yml", encoding="utf-8"))
body = open(f"{S}/ep{n}.md", encoding="utf-8").read().strip()
plan = state["outline"][n - 1]
meta = {"title": f"{state['title']}, Episode {n}: {plan['title']}",
        "description": info["description"], "tags": "Series, Stories"}
post = g.save_post(meta, body, [], fiction=True, slug=f"{state['slug'][:40]}-episode-{n}",
                   extra={"series": state["title"], "series_id": state["slug"], "episode": n,
                          "episodes": state["total_episodes"], "image_prompt": info["image_prompt"]})
state["episodes"].append({"n": n, "title": plan["title"], "file": post.name, "summary": info["summary"]})
state["next_episode"] = n + 1
g.save_series(state_path, state)
with (state_path.parent / "image_prompts.md").open("a", encoding="utf-8") as f:
    f.write(f"## Episode {n}: {plan['title']}\n\nSave as: `images/{post.stem[11:]}.jpg`\n\n{info['image_prompt']}\n\n")
print("saved", post.name, "| next episode:", state["next_episode"])

# Featured image for the new episode (and any other post still missing one).
import images
images.main()
