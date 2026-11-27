"""Entrypoint: serve harness.solve over the evaluator's protocol on $PORT."""
import harness
import taskbridge

if __name__ == "__main__":
    taskbridge.run(harness.solve)
