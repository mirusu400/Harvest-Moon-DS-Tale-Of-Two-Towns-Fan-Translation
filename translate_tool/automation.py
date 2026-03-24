import subprocess

GEMINI_PATH = "GEMINI.md"


def load_gemini():
    with open(GEMINI_PATH, "r", encoding="utf-8") as f:
        return f.read()


def run_codex(user_prompt: str):
    gemini = load_gemini()

    full_prompt = f"""
{gemini}
"""

    result = subprocess.run(
        ["codex", "exec", "-"],
        input=full_prompt,
        capture_output=True,
        text=True,
        shell=True,
        encoding="utf-8",
    )

    return result.stdout


prompt = input(">>> start? (y/n): ")
if prompt == "n":
    exit()

# 반복 실행
for i in range(50):
    print(f">>> iteration {i+1} start.")
    result = run_codex(prompt)
    print(result)
    print(f">>> iteration {i+1} complete. Continue.")
    print(f"==================== Iteration {i+1} ====================")
