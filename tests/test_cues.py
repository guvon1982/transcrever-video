from transcrever_video.cues import Cue, rebuild_cues, render_srt

OLD = [
    Cue(0.0, 5.0, "Bom dia, pessoal."),
    Cue(5.0, 10.0, "Hoje vamos falar de banco de dados."),
    Cue(10.0, 15.0, "Vamos começar."),
]
TEXT = "Bom dia, pessoal. Hoje vamos falar de banco de dados. Vamos começar."


def texts(cues):
    return [c.text for c in cues]


def times(cues):
    return [(c.start, c.end) for c in cues]


def test_sem_mudanca_mantem_tudo_igual():
    assert rebuild_cues(OLD, TEXT) == OLD


def test_o_layout_do_texto_nao_importa():
    # um parágrafo por linha, linhas em branco, tudo numa linha só: a legenda é a mesma
    assert rebuild_cues(OLD, "\n".join(c.text for c in OLD)) == OLD
    assert (
        rebuild_cues(
            OLD, "Bom dia, pessoal.\n\nHoje vamos falar de banco de dados.\n\nVamos começar.\n"
        )
        == OLD
    )
    assert rebuild_cues(OLD, "  " + TEXT.replace(" ", "   ") + "  ") == OLD


def test_dividir_ou_juntar_paragrafos_nao_mexe_na_legenda():
    dividido = "Bom dia, pessoal. Hoje vamos\n\nfalar de banco de dados. Vamos começar."
    assert rebuild_cues(OLD, dividido) == OLD


def test_editar_palavras_mantem_os_horarios():
    text = "Bom dia, Guvon. Hoje vamos falar de banco de dados. Vamos começar."
    cues = rebuild_cues(OLD, text)
    assert texts(cues) == [
        "Bom dia, Guvon.",
        "Hoje vamos falar de banco de dados.",
        "Vamos começar.",
    ]
    assert times(cues) == times(OLD)


def test_apagar_um_trecho_inteiro_tira_ele_da_legenda():
    text = "Bom dia, pessoal. Vamos começar."
    assert rebuild_cues(OLD, text) == [OLD[0], OLD[2]]


def test_apagar_so_parte_do_trecho_mantem_o_resto_dele():
    text = "Bom dia, pessoal. Hoje vamos falar de dados. Vamos começar."
    cues = rebuild_cues(OLD, text)
    assert texts(cues)[1] == "Hoje vamos falar de dados."
    assert times(cues) == times(OLD)


def test_editar_uma_frase_e_apagar_a_vizinha_funciona_sem_ambiguidade():
    # No modelo por palavra não há mais a ambiguidade do modelo por linha.
    text = "Olá, Guvon. Hoje vamos falar de banco de dados. Vamos começar."
    cues = rebuild_cues(OLD, text)
    assert texts(cues) == ["Olá, Guvon.", "Hoje vamos falar de banco de dados.", "Vamos começar."]
    assert times(cues) == times(OLD)


def test_palavras_novas_entram_no_trecho_da_palavra_anterior():
    text = "Bom dia, pessoal. Atenção! Hoje vamos falar de banco de dados. Vamos começar."
    cues = rebuild_cues(OLD, text)
    assert texts(cues)[0] == "Bom dia, pessoal. Atenção!"
    assert times(cues) == times(OLD)


def test_palavras_novas_no_comeco_entram_no_primeiro_trecho():
    cues = rebuild_cues(OLD, "Atenção! " + TEXT)
    assert texts(cues)[0] == "Atenção! Bom dia, pessoal."
    assert times(cues) == times(OLD)


def test_palavras_novas_no_fim_entram_no_ultimo_trecho():
    cues = rebuild_cues(OLD, TEXT + " Obrigado.")
    assert texts(cues)[-1] == "Vamos começar. Obrigado."


def test_troca_por_mais_palavras_fica_no_trecho_onde_estava():
    text = "Bom dia, pessoal. Hoje vamos conversar sobre banco de dados relacionais. Vamos começar."
    cues = rebuild_cues(OLD, text)
    assert texts(cues)[1] == "Hoje vamos conversar sobre banco de dados relacionais."
    assert times(cues) == times(OLD)


def test_apagar_tudo_nao_deixa_trechos():
    assert rebuild_cues(OLD, "") == []
    assert rebuild_cues(OLD, "\n\n  \n") == []


def test_substituir_tudo_por_texto_novo_espalha_pelos_trechos_em_ordem():
    cues = rebuild_cues(OLD, "um dois três quatro cinco seis sete oito nove dez onze doze")
    assert " ".join(texts(cues)) == "um dois três quatro cinco seis sete oito nove dez onze doze"
    assert all(a.start < b.start for a, b in zip(cues, cues[1:], strict=False))


def test_sem_trechos_antigos_nao_ha_como_montar():
    assert rebuild_cues([], "alguma coisa\n") == []


def test_texto_grande_com_poucas_edicoes_e_rapido():
    import random
    import time

    rng = random.Random(7)  # ~2 h de aula: 20 mil palavras de um vocabulário de 3 mil
    words = [f"p{rng.randrange(3000)}" for _ in range(20_000)]
    old = [Cue(i * 5.0, i * 5.0 + 5.0, " ".join(words[i * 10 : i * 10 + 10])) for i in range(2000)]
    edited = words.copy()
    edited[500] = "TROCADA"
    del edited[9000:9010]
    started = time.perf_counter()
    cues = rebuild_cues(old, " ".join(edited))
    assert time.perf_counter() - started < 10
    assert "TROCADA" in cues[50].text
    assert len(cues) == 1999  # o trecho 900 foi inteiro apagado


def test_render_srt_numera_a_partir_de_1():
    srt = render_srt([Cue(0.0, 1.5, "Olá."), Cue(1.5, 3.0, "Tchau.")])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,500\nOlá.\n\n2\n00:00:01,500 --> 00:00:03,000\nTchau.\n\n"
    )
    assert render_srt([]) == ""
