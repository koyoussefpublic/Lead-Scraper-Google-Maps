import sys
from pathlib import Path

# Make the project root importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure prompt_toolkit doesn't fail on Windows in headless/non-console test runners
try:
    from prompt_toolkit.output import DummyOutput, create_output
    try:
        create_output()
    except Exception:
        import prompt_toolkit.output.defaults
        prompt_toolkit.output.defaults.create_output = lambda **kwargs: DummyOutput()
except Exception:
    pass

