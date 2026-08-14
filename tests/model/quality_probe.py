"""Sonda manual, sem mídia real, para avaliar o modelo configurado."""

import json
import tempfile
from pathlib import Path

import pysubs2

import anime_subtitle_translator as translator


SAMPLES = [
    "No way! You're kidding, right?",
    "It's not like I did it for you or anything.",
    "You'd better get going before it starts raining.",
    "Don't call me 'senpai' in front of everyone.",
    "冗談じゃない！そんな約束、聞いてないよ。",
    "大丈夫、私がそばにいるから。",
    "この一撃で終わらせる！",
    "また遅刻？信じられない。",
]

ASS_FIXTURE = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: OP,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\an8}Don't move!\\NI'll handle this.
Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,This is a piece of cake.
Dialogue: 0,0:00:04.00,0:00:06.00,OP,,0,0,0,,{\\k30}La la la
"""


def main():
    translated = translator.translate_batch(SAMPLES, context="conversa informal entre estudantes")

    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "fixture.eng.ass"
        output = Path(tmp_dir) / "fixture.pt-BR.ass"
        source.write_text(ASS_FIXTURE, encoding="utf-8")
        original = pysubs2.load(str(source), encoding="utf-8")
        translator.translate_subtitle_file(source, output)
        result = pysubs2.load(str(output), encoding="utf-8")

        format_report = {
            "output_created": output.exists(),
            "event_count_preserved": len(original) == len(result),
            "position_tag_preserved": r"{\an8}" in result[0].text,
            "linebreak_preserved": r"\N" in result[0].text,
            "karaoke_line_unchanged": original[2].text == result[2].text,
            "translated_ass_lines": [result[0].text, result[1].text],
        }

    print(json.dumps({"samples": list(zip(SAMPLES, translated)), "format": format_report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
