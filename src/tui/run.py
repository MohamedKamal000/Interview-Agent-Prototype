import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .app import InterviewTUI


def main():
    app = InterviewTUI()
    app.run()


if __name__ == "__main__":
    main()
