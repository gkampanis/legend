"""Run the unchanged test suite with durable logs and GitHub annotations."""
import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def annotation(message):
    return message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    evidence = ROOT / 'build-evidence'
    evidence.mkdir(exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.discover('tests'))
    output = stream.getvalue()
    print(output)
    (evidence / 'test-results.txt').write_text(output, encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as file:
            file.write('### Automated tests\n\n```text\n' + output + '\n```\n')
    for test, traceback in result.failures + result.errors:
        print('::error title=' + annotation(str(test)) + '::' + annotation(traceback))
    if result.skipped or result.testsRun == 0:
        print('::error::All tests must execute; skipped or zero-test runs do not validate this build.')
    return 0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
