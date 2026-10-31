import pytest

from transcrever_video.paragraphs import ParagraphBuilder, ends_sentence

FRASE = "Esta é uma frase de tamanho razoável para o teste de parágrafos."  # 64 caracteres


def build(segments, **options):
    """Monta o texto completo (como o .txt) a partir de (texto, início, fim)."""
    builder = ParagraphBuilder(**options)
    return "".join(builder.feed(text, start, end) + text for text, start, end in segments)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Acabou.", True),
        ("Será?", True),
        ("Nossa!", True),
        ("E então…", True),
        ('Ele disse "acabou."', True),
        ("Ele disse (acabou.)", True),
        ("Acabou. ", True),
        ("Ainda não acabou,", False),
        ("sem pontuação nenhuma", False),
        ("", False),
    ],
)
def test_ends_sentence(text, expected):
    assert ends_sentence(text) is expected


def test_primeiro_trecho_nao_tem_separador():
    assert ParagraphBuilder().feed("Olá.", 0.0, 1.0) == ""


def test_trechos_curtos_ficam_no_mesmo_paragrafo_mesmo_com_pausa():
    texto = build([("Oi.", 0, 1), ("Tudo bem?", 3, 4)])  # pausa de 2 s, mas parágrafo pequeno
    assert texto == "Oi. Tudo bem?"


def test_pausa_depois_de_frase_abre_paragrafo_quando_ja_tem_tamanho():
    segments = [(FRASE, i * 5.0, i * 5.0 + 4.0) for i in range(4)]  # 4 frases coladas
    segments.append(("Outro assunto.", 4 * 5.0 + 2.0, 4 * 5.0 + 4.0))  # pausa de 2 s depois
    texto = build(segments)
    assert texto.endswith(FRASE + "\n\nOutro assunto.")
    assert texto.count("\n\n") == 1


def test_sem_pausa_nao_abre_paragrafo():
    segments = [(FRASE, i * 5.0, i * 5.0 + 5.0) for i in range(8)]  # sem nenhuma pausa
    assert "\n\n" not in build(segments)


def test_nunca_corta_no_meio_de_uma_frase_mesmo_com_pausa_longa():
    segments = [(FRASE, i * 5.0, i * 5.0 + 5.0) for i in range(4)]  # frases coladas, sem pausa
    segments.append(("continua sem ponto final,", 20.0, 21.0))
    segments.append(("e termina aqui depois da pausa longa.", 30.0, 31.0))  # pausa de 9 s
    # o trecho anterior não terminou a frase, então não pode haver quebra antes dele
    assert "\n\n" not in build(segments)


def test_paragrafo_muito_grande_fecha_na_proxima_frase_mesmo_sem_pausa():
    segments = [(FRASE, i * 5.0, i * 5.0 + 5.0) for i in range(20)]  # 20 frases coladas
    paragrafos = build(segments).split("\n\n")
    assert len(paragrafos) > 1
    assert all(p.endswith(".") for p in paragrafos)  # sempre fecha em fim de frase
    assert all(len(p) <= 700 + len(FRASE) + 1 for p in paragrafos)


def test_texto_sem_pontuacao_tem_um_limite_de_seguranca():
    palavra = "palavra sem pontuação nenhuma por aqui"  # nunca termina frase
    segments = [(palavra, i * 2.0, i * 2.0 + 2.0) for i in range(80)]
    paragrafos = build(segments).split("\n\n")
    assert len(paragrafos) > 1  # não vira um bloco único gigante
    assert all(len(p) < 2 * 700 + 100 for p in paragrafos)


def test_dois_pontos_de_pausa_configuravel():
    segments = [(FRASE, i * 5.0, i * 5.0 + 4.0) for i in range(4)]
    segments.append(("Depois.", 20.3, 21.0))  # a frase 4 termina em 19.0: pausa de 1,3 s
    # com o limite padrão (0,45 s) 1,3 s é pausa; com limite de 5 s não é
    assert "\n\n" in build(segments)
    assert "\n\n" not in build(segments, pause=5.0)


def test_concatenar_os_pedacos_reconstroi_o_texto_sem_perder_nada():
    segments = [(f"Frase número {i}.", i * 3.0, i * 3.0 + 2.0) for i in range(60)]
    texto = build(segments)
    assert texto.replace("\n\n", " ").split() == " ".join(t for t, _, _ in segments).split()
