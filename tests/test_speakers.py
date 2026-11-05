import random

from transcrever_video.speakers import (
    SpeakerSpan,
    Turn,
    Word,
    build_turns,
    reassign_turn,
    render_srt,
    render_txt,
    speaker_name,
)


def words(*items):
    """('texto', início, fim) -> Word."""
    return [Word(start, end, text) for text, start, end in items]


SPANS = [SpeakerSpan(0.0, 5.0, 0), SpeakerSpan(5.0, 9.0, 1), SpeakerSpan(9.0, 12.0, 0)]
WORDS = words(
    (" Bom", 0.5, 0.9),
    (" dia", 1.0, 1.4),
    (" Oi", 5.2, 5.5),
    (" tudo", 5.6, 6.0),
    (" bem?", 6.1, 6.5),
    (" Vamos", 9.5, 9.9),
    (" começar.", 10.0, 10.6),
)


def test_palavras_seguidas_do_mesmo_falante_formam_um_turno():
    turns = build_turns(WORDS, SPANS)
    assert turns == [
        Turn(0, 0.5, 1.4, "Bom dia"),
        Turn(1, 5.2, 6.5, "Oi tudo bem?"),
        Turn(0, 9.5, 10.6, "Vamos começar."),
    ]


def test_palavra_vai_para_o_falante_que_ocupa_mais_tempo_dela():
    # palavra de 4,8 a 5,6: 0,2 s com o falante 0 e 0,6 s com o falante 1
    turns = build_turns(words(("meio", 4.8, 5.6)), SPANS)
    assert turns[0].speaker == 1


def test_palavra_sem_sobreposicao_vai_para_o_trecho_mais_proximo():
    spans = [SpeakerSpan(0.0, 2.0, 0), SpeakerSpan(10.0, 12.0, 1)]
    # 3,0 está a 1 s do primeiro trecho e a 7 s do segundo; 9,0 está a 1 s do segundo
    turns = build_turns(words(("a", 3.0, 3.2), ("b", 9.0, 9.2)), spans)
    assert [t.speaker for t in turns] == [0, 1]


def test_sem_trechos_de_falante_tudo_e_do_falante_zero():
    assert build_turns(WORDS, []) == [Turn(0, 0.5, 10.6, "Bom dia Oi tudo bem? Vamos começar.")]


def test_sem_palavras_nao_ha_turnos():
    assert build_turns([], SPANS) == []


def test_palavras_vazias_sao_ignoradas():
    turns = build_turns(words(("  ", 0.1, 0.2), (" oi", 0.3, 0.5)), SPANS)
    assert turns == [Turn(0, 0.3, 0.5, "oi")]


def test_trechos_sobrepostos_escolhem_quem_domina_a_palavra():
    spans = [SpeakerSpan(0.0, 10.0, 0), SpeakerSpan(4.0, 6.0, 1)]
    # 'longa' tem 4 s com o falante 0 e 2 s com o falante 1
    turns = build_turns(words(("longa", 3.0, 7.0)), spans)
    assert turns[0].speaker == 0


def test_resultado_e_igual_a_uma_busca_ingenua_em_dados_aleatorios():
    rng = random.Random(7)
    spans, t = [], 0.0
    for _ in range(300):
        length = rng.uniform(0.3, 6.0)
        spans.append(SpeakerSpan(t, t + length, rng.randrange(3)))
        t += length + rng.choice([0.0, 0.0, rng.uniform(0.0, 2.0)])
    ws, t = [], 0.0
    while t < spans[-1].end:
        length = rng.uniform(0.1, 0.9)
        ws.append(Word(t, t + length, f"p{len(ws)}"))
        t += length + rng.uniform(0.0, 0.4)

    def naive(word):
        best, best_overlap = None, 0.0
        for s in spans:
            overlap = min(word.end, s.end) - max(word.start, s.start)
            if overlap > best_overlap:
                best, best_overlap = s.speaker, overlap
        if best is None:
            mid = (word.start + word.end) / 2
            best = min(spans, key=lambda s: min(abs(mid - s.start), abs(mid - s.end))).speaker
        return best

    expected = [naive(w) for w in ws]
    got = []
    for turn in build_turns(ws, spans):
        got += [turn.speaker] * len(turn.text.split())
    assert got == expected


def test_nome_padrao_e_nome_dado():
    assert speaker_name({}, 0) == "Falante 1"
    assert speaker_name({1: "Ana"}, 1) == "Ana"
    assert speaker_name({1: "  "}, 1) == "Falante 2"


def test_txt_tem_nome_e_linha_em_branco_entre_turnos():
    turns = build_turns(WORDS, SPANS)
    assert render_txt(turns, {1: "Ana"}) == (
        "Falante 1: Bom dia\n\nAna: Oi tudo bem?\n\nFalante 1: Vamos começar.\n"
    )
    assert render_txt([]) == ""


def test_srt_tem_o_nome_no_comeco_de_cada_bloco():
    srt = render_srt([Turn(0, 0.5, 1.4, "Bom dia"), Turn(1, 5.2, 6.5, "Oi")], {0: "Gustavo"})
    assert srt == (
        "1\n00:00:00,500 --> 00:00:01,400\nGustavo: Bom dia\n\n"
        "2\n00:00:05,200 --> 00:00:06,500\nFalante 2: Oi\n\n"
    )


def test_renomear_muda_todos_os_turnos_do_falante():
    turns = build_turns(WORDS, SPANS)
    txt = render_txt(turns, {0: "Gustavo"})
    assert txt.count("Gustavo:") == 2
    assert "Falante 1" not in txt


def test_corrigir_o_falante_de_um_turno_junta_com_os_vizinhos():
    turns = [Turn(0, 0, 1, "a"), Turn(1, 1, 2, "b"), Turn(0, 2, 3, "c")]
    assert reassign_turn(turns, 1, 0) == [Turn(0, 0, 3, "a b c")]


def test_corrigir_o_falante_para_outro_mantem_os_turnos_separados():
    turns = [Turn(0, 0, 1, "a"), Turn(1, 1, 2, "b"), Turn(0, 2, 3, "c")]
    assert reassign_turn(turns, 1, 2) == [
        Turn(0, 0, 1, "a"),
        Turn(2, 1, 2, "b"),
        Turn(0, 2, 3, "c"),
    ]
