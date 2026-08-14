import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pysubs2

import anime_subtitle_translator as translator


class TranslatorSafetyTests(unittest.TestCase):
    def test_generated_pt_br_subtitle_is_recognized(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video = Path(tmp_dir) / "episode.mkv"
            video.touch()
            video.with_suffix(".pt-BR.ass").write_text("[Script Info]\n", encoding="utf-8")

            self.assertTrue(translator.has_pt_subtitle(video))

    def test_missing_subtitle_is_not_recognized(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video = Path(tmp_dir) / "episode.mkv"
            video.touch()

            self.assertFalse(translator.has_pt_subtitle(video))

    def test_pt_br_ssa_subtitle_is_recognized(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video = Path(tmp_dir) / "episode.mkv"
            video.touch()
            video.with_suffix(".pt-BR.ssa").touch()

            self.assertTrue(translator.has_pt_subtitle(video))

    def test_recent_video_waits_for_stability(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video = Path(tmp_dir) / "episode.mkv"
            video.touch()
            with patch.object(translator, "MIN_FILE_AGE_SECONDS", 600):
                self.assertFalse(translator.is_ready_for_translation(video))

            old_timestamp = time.time() - 601
            os.utime(video, (old_timestamp, old_timestamp))
            with patch.object(translator, "MIN_FILE_AGE_SECONDS", 600):
                self.assertTrue(translator.is_ready_for_translation(video))

    def test_partial_name_is_never_ready_for_translation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video = Path(tmp_dir) / "episode.part.mkv"
            video.touch()
            old_timestamp = time.time() - 3600
            os.utime(video, (old_timestamp, old_timestamp))

            self.assertFalse(translator.is_ready_for_translation(video))

    def test_cp1252_subtitle_uses_encoding_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "episode.eng.srt"
            source.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nOl\xe1\n")

            self.assertEqual(translator.load_subtitles(source)[0].text, "Olá")

    def test_glossary_terms_are_restored_with_the_configured_value(self):
        source = r"{\an8}Call the Hero Association, senpai!"
        protected, tag_map, glossary_map = translator.protect_text(
            source,
            {"Hero Association": "Associação de Heróis", "senpai": "senpai"},
        )

        self.assertNotIn("Hero Association", protected)
        self.assertNotIn("senpai", protected)
        self.assertEqual(
            translator.restore_text(protected, tag_map, glossary_map),
            r"{\an8}Call the Associação de Heróis, senpai!",
        )

    def test_series_glossary_overrides_default_terms(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "shows"
            folder = root / "Example Anime" / "Season 1"
            folder.mkdir(parents=True)
            glossary_file = Path(tmp_dir) / "glossary.json"
            glossary_file.write_text(
                json.dumps(
                    {
                        "default": {"terms": {"Hero": "Herói"}},
                        "series": {"Example Anime": {"terms": {"Hero": "Campeão", "senpai": "senpai"}}},
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(translator, "BASE_LIBRARY", root):
                with patch.object(translator, "GLOSSARY_FILE", glossary_file):
                    terms = translator.load_glossary_for_folder(folder)

            self.assertEqual(terms, {"Hero": "Campeão", "senpai": "senpai"})

    def test_glossary_mapping_is_applied_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "episode.eng.srt"
            output = Path(tmp_dir) / "episode.pt-BR.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHero Association\n",
                encoding="utf-8",
            )

            with patch.object(translator, "translate_lines_robust", side_effect=lambda lines: (lines, 0)):
                translator.translate_subtitle_file(
                    source,
                    output,
                    glossary={"Hero Association": "Associação de Heróis"},
                )

            self.assertEqual(translator.load_subtitles(output)[0].text, "Associação de Heróis")

    def test_glossary_only_line_is_not_considered_untranslated(self):
        self.assertFalse(translator.is_effectively_untranslated("§G0§", "§G0§"))

    def test_ass_plan_never_sends_tags_or_newlines_to_the_model(self):
        source = r"{\an8}Hello {\i1}world{\i0}!\NGoodbye"
        plan, fragments = translator.build_ass_translation_plan(source, {}, 0)

        self.assertEqual(fragments, ["Hello ", "world", "Goodbye"])
        self.assertEqual(
            [piece for piece in plan if piece[0] == "static"],
            [
                ("static", r"{\an8}"),
                ("static", r"{\i1}"),
                ("static", r"{\i0}"),
                ("static", "!"),
                ("static", r"\N"),
            ],
        )
        self.assertTrue(all("{" not in fragment and r"\N" not in fragment for fragment in fragments))

    def test_ass_markup_is_reassembled_exactly_after_fragment_translation(self):
        source = r"{\an8}Hero Association{\i1} is ready{\i0}.\NGoodbye"
        with patch.object(
            translator,
            "translate_lines_robust",
            return_value=([" está pronta", "Tchau"], 0),
        ):
            translated, failures = translator.translate_texts_preserving_ass_markup(
                [source],
                {"Hero Association": "Associação de Heróis"},
                "episode.ass",
            )

        self.assertEqual(failures, 0)
        self.assertEqual(
            translated,
            [r"{\an8}Associação de Heróis{\i1} está pronta{\i0}.\NTchau"],
        )

    def test_context_has_neighboring_dialogue_but_no_ass_markup(self):
        contexts = translator.build_translation_contexts(
            [r"{\an8}I will wait.\NDo not worry.", "We should leave now."]
        )

        self.assertIn("Fala completa atual: I will wait. Do not worry.", contexts[0])
        self.assertIn("Próxima fala: We should leave now.", contexts[0])
        self.assertIn("Fala anterior: I will wait. Do not worry.", contexts[1])
        self.assertNotIn(r"{\an8}", contexts[0])
        self.assertNotIn(r"\N", contexts[0])

    def test_glossary_only_ass_fragment_is_restored_without_model_call(self):
        plan, fragments = translator.build_ass_translation_plan(
            "Hero Association!", {"Hero Association": "Associação de Heróis"}, 0
        )

        self.assertEqual(fragments, [])
        self.assertEqual(plan, [("static", "Associação de Heróis!")])

    def test_single_word_is_allowed_when_it_may_be_a_proper_name(self):
        self.assertFalse(translator.is_effectively_untranslated("Eren!", "Eren!"))

    def test_omitted_leading_ass_tag_is_restored_deterministically(self):
        self.assertEqual(
            translator.restore_omitted_leading_style_tags("§T0§Hello", "Olá"),
            "§T0§Olá",
        )

    def test_internal_or_partially_preserved_ass_tag_is_not_inferred(self):
        self.assertEqual(
            translator.restore_omitted_leading_style_tags("Hello §T0§there", "Olá aí"),
            "Olá aí",
        )
        self.assertEqual(
            translator.restore_omitted_leading_style_tags("§T0§§T1§Hello", "§T1§Olá"),
            "§T1§Olá",
        )

    def test_structured_translation_response_is_parsed(self):
        self.assertEqual(
            translator.parse_translation_response('{"output": ["Olá", "Tchau"]}', 2),
            ["Olá", "Tchau"],
        )

    def test_known_model_aliases_are_accepted_for_compatibility(self):
        self.assertEqual(
            translator.parse_translation_response('{"translations": ["Olá"]}', 1),
            ["Olá"],
        )
        self.assertEqual(
            translator.parse_translation_response('{"translation": ["Olá"]}', 1),
            ["Olá"],
        )

    def test_translation_response_with_extra_formatting_is_recovered(self):
        self.assertEqual(
            translator.parse_translation_response('Resposta:\n```json\n{"output": ["Olá"]}\n```', 1),
            ["Olá"],
        )

    def test_translation_response_rejects_wrong_number_of_lines(self):
        with self.assertRaisesRegex(ValueError, "esperava 2 linhas, recebi 1"):
            translator.parse_translation_response('{"output": ["Olá"]}', 2)

    def test_review_response_requires_a_known_verdict_and_text(self):
        self.assertEqual(
            translator.parse_review_response('{"verdict": "correct", "translation": "Olá"}'),
            ("correct", "Olá"),
        )
        with self.assertRaisesRegex(ValueError, "veredito"):
            translator.parse_review_response('{"verdict": "reject", "translation": "Olá"}')

    def test_local_reviewer_accepts_a_valid_correction(self):
        response = MagicMock()
        response.json.return_value = {
            "message": {"content": '{"verdict": "correct", "translation": "Olá, meu amigo."}'}
        }
        with patch.object(translator, "REVIEW_MODEL", "llama3.1:8b"):
            with patch.object(translator.requests, "post", return_value=response) as post:
                reviewed = translator.review_translation_candidate(
                    "Hello, my friend.", "Olá, amigo.", "Fala anterior: teste."
                )

        self.assertEqual(reviewed, "Olá, meu amigo.")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "llama3.1:8b")
        self.assertEqual(post.call_args.kwargs["json"]["format"]["required"], ["verdict", "translation"])

    def test_local_reviewer_rejects_a_correction_with_english_residue(self):
        response = MagicMock()
        response.json.return_value = {
            "message": {"content": '{"verdict": "correct", "translation": "Olá, friend."}'}
        }
        with patch.object(translator, "REVIEW_MODEL", "llama3.1:8b"):
            with patch.object(translator.requests, "post", return_value=response):
                with self.assertRaisesRegex(ValueError, "correção do revisor rejeitada"):
                    translator.review_translation_candidate(
                        "Hello, friend.", "Olá, amigo.", "Fala anterior: teste."
                    )

    def test_reviewer_skips_fragments_with_protected_markers(self):
        with patch.object(translator, "REVIEW_MODEL", "llama3.1:8b"):
            with patch.object(translator, "review_translation_candidate") as review:
                reviewed = translator.review_suspicious_fragments(
                    ["§G0§, please"],
                    ["§G0§, por favor"],
                    ["Fala completa atual: termo protegido"],
                    [{"fallback"}],
                )

        self.assertEqual(reviewed, ["§G0§, por favor"])
        review.assert_not_called()

    def test_translate_batch_requests_ollama_structured_output(self):
        response = MagicMock()
        response.json.return_value = {"message": {"content": '{"output": ["Olá"]}'}}
        with patch.object(translator.requests, "post", return_value=response) as post:
            self.assertEqual(translator.translate_batch(["Hello"]), ["Olá"])

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["format"]["type"], "object")
        self.assertEqual(payload["format"]["properties"]["output"]["minItems"], 1)
        self.assertEqual(payload["options"]["temperature"], 0)

    def test_translate_batch_can_select_the_fallback_model(self):
        response = MagicMock()
        response.json.return_value = {"message": {"content": '{"output": ["Olá"]}'}}
        with patch.object(translator.requests, "post", return_value=response) as post:
            self.assertEqual(
                translator.translate_batch(["Hello"], model_name="llama3.1:8b"),
                ["Olá"],
            )

        self.assertEqual(post.call_args.kwargs["json"]["model"], "llama3.1:8b")

    def test_translate_batch_sends_context_only_as_reference(self):
        response = MagicMock()
        response.json.return_value = {"message": {"content": '{"output": ["Olá"]}'}}
        with patch.object(translator.requests, "post", return_value=response) as post:
            self.assertEqual(
                translator.translate_batch(["Hello"], contexts=["Fala anterior: Wait."]),
                ["Olá"],
            )

        prompt = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("Traduza SOMENTE `texto_alvo`", prompt)
        self.assertIn('"contexto": "Fala anterior: Wait."', prompt)

    def test_translate_batch_restores_an_omitted_leading_ass_tag(self):
        response = MagicMock()
        response.json.return_value = {"message": {"content": '{"output": ["Olá"]}'}}
        with patch.object(translator.requests, "post", return_value=response):
            self.assertEqual(translator.translate_batch(["§T0§Hello"]), ["§T0§Olá"])

    def test_mixed_batch_retries_only_the_unchanged_line(self):
        with patch.object(
            translator,
            "translate_batch",
            side_effect=[
                ["Olá, pessoal", "Goodbye everyone"],
                ["Goodbye everyone"],
                ["Adeus, pessoal"],
            ],
        ) as translate_batch:
            translated, failures = translator.translate_lines_robust(["Hello everyone", "Goodbye everyone"])

        self.assertEqual(translated, ["Olá, pessoal", "Adeus, pessoal"])
        self.assertEqual(failures, 0)
        self.assertEqual(translate_batch.call_args_list[-1].args[0], ["Goodbye everyone"])

    def test_persistently_unchanged_line_is_reported_as_failure(self):
        with patch.object(translator, "translate_batch", return_value=["Hello there"]):
            translated, failures = translator.translate_lines_robust(["Hello there"])

        self.assertEqual(translated, ["Hello there"])
        self.assertEqual(failures, 1)

    def test_batch_with_invalid_protocol_is_split_before_individual_retry(self):
        with patch.object(
            translator,
            "translate_batch",
            side_effect=[
                ValueError("quantidade incorreta"),
                ["Olá", "Tchau"],
                ["Até logo", "Volte sempre"],
            ],
        ) as translate_batch:
            translated, failures = translator.translate_lines_robust(
                ["Hello", "Goodbye", "See you", "Come back"]
            )

        self.assertEqual((translated, failures), (["Olá", "Tchau", "Até logo", "Volte sempre"], 0))
        self.assertEqual([len(call.args[0]) for call in translate_batch.call_args_list], [4, 2, 2])

    def test_partial_english_residue_is_detected_but_single_name_is_allowed(self):
        self.assertEqual(
            translator.untranslated_english_residue("The enemy is here.", "O enemy está aqui."),
            ["enemy"],
        )
        self.assertEqual(translator.untranslated_english_residue("Eren!", "Eren!"), [])

    def test_partial_english_residue_is_retried_individually(self):
        with patch.object(
            translator,
            "translate_batch",
            side_effect=[["O enemy está aqui."], ["O inimigo está aqui."]],
        ):
            translated, failures = translator.translate_lines_robust(["The enemy is here."])

        self.assertEqual((translated, failures), (["O inimigo está aqui."], 0))

    def test_retry_marks_a_fragment_for_post_translation_review(self):
        review_reasons = [set()]
        with patch.object(
            translator,
            "translate_batch",
            side_effect=[["Hello, friend."], ["Olá, amigo."]],
        ):
            translated, failures = translator.translate_lines_robust(
                ["Hello, friend."], review_reasons=review_reasons
            )

        self.assertEqual((translated, failures), (["Olá, amigo."], 0))
        self.assertEqual(review_reasons, [{"retry"}])

    def test_plain_line_uses_fallback_only_after_primary_retries_fail(self):
        primary_error = RuntimeError("qwen indisponível")
        with patch.object(translator, "FALLBACK_OLLAMA_MODEL", "llama3.1:8b"):
            with patch.object(
                translator,
                "translate_batch",
                side_effect=[primary_error, primary_error, primary_error, ["Olá, pessoal"]],
            ) as translate_batch:
                translated, failures = translator.translate_lines_robust(["Hello, everyone"])

        self.assertEqual((translated, failures), (["Olá, pessoal"], 0))
        self.assertEqual(translate_batch.call_count, 4)
        self.assertEqual(translate_batch.call_args_list[-1].kwargs["model_name"], "llama3.1:8b")

    def test_fallback_is_not_used_when_the_line_has_a_protected_marker(self):
        with patch.object(translator, "FALLBACK_OLLAMA_MODEL", "llama3.1:8b"):
            with patch.object(
                translator,
                "translate_batch",
                side_effect=RuntimeError("qwen indisponível"),
            ) as translate_batch:
                translated, failures = translator.translate_lines_robust(["§G0§, please"])

        self.assertEqual((translated, failures), (["§G0§, please"], 1))
        self.assertEqual(translate_batch.call_count, 3)

    def test_atomic_save_creates_final_file_without_temporary_artifact(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "episode.pt-BR.ass"
            subs = pysubs2.SSAFile()
            subs.events.append(pysubs2.SSAEvent(start=0, end=1000, text="Olá"))

            translator.save_subtitles_atomically(subs, output)

            self.assertTrue(output.exists())
            self.assertEqual(pysubs2.load(str(output), encoding="utf-8")[0].text, "Olá")
            self.assertEqual(list(Path(tmp_dir).glob(".*.subtranslate-*.tmp.ass")), [])

    def test_atomic_save_does_not_overwrite_existing_final_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "episode.pt-BR.ass"
            output.write_text("original", encoding="utf-8")
            subs = pysubs2.SSAFile()
            subs.events.append(pysubs2.SSAEvent(start=0, end=1000, text="novo"))

            with self.assertRaises(FileExistsError):
                translator.save_subtitles_atomically(subs, output)

            self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_failed_individual_lines_are_reported(self):
        with patch.object(translator, "translate_batch", side_effect=RuntimeError("offline")):
            translated, failures = translator.translate_lines_robust(["one", "two"])

        self.assertEqual(translated, ["one", "two"])
        self.assertEqual(failures, 2)

    def test_incomplete_translation_does_not_publish_final_subtitle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "episode.eng.srt"
            output = Path(tmp_dir) / "episode.pt-BR.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                encoding="utf-8",
            )

            with patch.object(translator, "translate_lines_robust", return_value=(["Hello"], 1)):
                with self.assertRaises(translator.TranslationIncompleteError):
                    translator.translate_subtitle_file(source, output)

            self.assertFalse(output.exists())

    def test_unchanged_line_cannot_be_published_even_if_the_retry_layer_regresses(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "episode.eng.srt"
            output = Path(tmp_dir) / "episode.pt-BR.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello there\n",
                encoding="utf-8",
            )

            with patch.object(translator, "translate_lines_robust", return_value=(["Hello there"], 0)):
                with self.assertRaises(translator.TranslationIncompleteError):
                    translator.translate_subtitle_file(source, output)

            self.assertFalse(output.exists())

    def test_verify_detects_an_english_fragment_even_with_a_glossary_term(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video = root / "episode.mkv"
            original = root / "source.eng.srt"
            final_sub = root / "episode.pt-BR.srt"
            video.touch()
            original.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nCall the Hero Association!\n",
                encoding="utf-8",
            )
            final_sub.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nCall the Hero Association!\n",
                encoding="utf-8",
            )

            def copy_original(_video, _index, destination):
                shutil.copyfile(original, destination)

            with patch.object(translator, "find_subtitle_stream", return_value=(2, "eng", ".srt")):
                with patch.object(translator, "extract_subtitle", side_effect=copy_original):
                    with patch.object(
                        translator,
                        "translate_lines_robust",
                        return_value=(["Ligue para a §G0§!"], 0),
                    ):
                        fixed, missing = translator.verify_and_fix_subtitle(
                            video,
                            final_sub,
                            glossary={"Hero Association": "Associação de Heróis"},
                        )

            self.assertEqual((fixed, missing), (1, 0))
            self.assertEqual(
                translator.load_subtitles(final_sub)[0].text,
                "Ligue para a Associação de Heróis!",
            )

    def test_process_folder_reports_translation_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            (folder / "episode.mkv").touch()

            with patch.object(translator, "find_subtitle_stream", return_value=(2, "eng", ".srt")):
                with patch.object(translator, "is_ready_for_translation", return_value=True):
                    with patch.object(translator, "extract_subtitle"):
                        with patch.object(
                            translator,
                            "translate_subtitle_file",
                            side_effect=translator.TranslationIncompleteError("incompleta"),
                        ):
                            self.assertEqual(translator.process_folder(folder), 1)


if __name__ == "__main__":
    unittest.main()
