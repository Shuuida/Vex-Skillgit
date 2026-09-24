import re

def parse_commit_intent(commit_message: str) -> str:
    """
    Extracts the Conventional Commit intent from a commit message.
    Returns 'feat', 'fix', 'docs', 'refactor', 'roll', 'branch', or 'standard'.
    """
    match = re.match(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|branch|roll)(\(.+\))?:", commit_message.lower())
    
    if match:
        return match.group(1)
    return "standard"