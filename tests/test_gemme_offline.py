"""GEMME never causes the Docker daemon to pull an undeclared image."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enzyme_core.skills.registry import resolve_skill_file


class GemmeOfflineTests(unittest.TestCase):
    def test_docker_run_disables_implicit_image_pull(self):
        source=resolve_skill_file('gemme','scripts/gemme_cli.py')
        spec=importlib.util.spec_from_file_location('gemme_no_pull_fixture',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);msa=root/'input.a2m';msa.write_text('>query\nACDE\n>homolog\nACDE\n')
            log=root/'docker-args.txt';docker=root/'docker'
            docker.write_text('#!/bin/sh\nif [ "$1" = image ]; then exit 0; fi\nprintf "%s\\n" "$@" > "$FAKE_DOCKER_LOG"\nexit 19\n')
            docker.chmod(0o755)
            args=module.build_parser().parse_args(['score','--msa',str(msa),'--sequence','ACDE','--mutations','A1C','--out-dir',str(root/'out')])
            with patch.dict(os.environ,{'PATH':str(root)+os.pathsep+os.environ.get('PATH',''),'FAKE_DOCKER_LOG':str(log)}):
                with self.assertRaisesRegex(RuntimeError,'exit code 19'):module.command_score(args)
            actual=log.read_text().splitlines()
            self.assertEqual(actual[:3],['run','--rm','--pull=never'])


if __name__=='__main__':unittest.main()
