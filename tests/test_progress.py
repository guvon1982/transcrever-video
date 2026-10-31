import pytest

from transcrever_video.progress import Progress, ProgressTracker, describe, format_duration


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_eta_nao_aparece_no_inicio():
    clock = FakeClock()
    tracker = ProgressTracker(1000, clock)
    clock.now += 2  # poucos segundos: estimativa ainda não confiável
    assert tracker.update(10).remaining is None


def test_eta_nao_aparece_com_menos_de_2_por_cento():
    clock = FakeClock()
    tracker = ProgressTracker(1000, clock)
    clock.now += 30
    assert tracker.update(10).remaining is None  # 1%


def test_eta_pela_velocidade_media():
    clock = FakeClock()
    tracker = ProgressTracker(100, clock)
    clock.now += 10  # 50 s de áudio em 10 s reais -> 5x
    progress = tracker.update(50)
    assert progress.fraction == 0.5
    assert progress.remaining == pytest.approx(10)  # restam 50 s de áudio, a 5x


def test_fraction_limitada_entre_0_e_1():
    assert Progress(150, 100, 1, None).fraction == 1.0
    assert Progress(-5, 100, 1, None).fraction == 0.0
    assert Progress(10, 0, 1, None).fraction == 0.0


def test_describe_nas_tres_fases():
    assert describe(Progress(1, 100, 2, None)) == (
        "1% • calculando o tempo restante… • decorrido 2 s"
    )
    assert describe(Progress(50, 100, 120, 125)) == (
        "50% • faltam ~2 min 05 s • decorrido 2 min 00 s"
    )
    assert describe(Progress(100, 100, 90, 0.0)) == "100% • concluído em 1 min 30 s"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (45, "45 s"),
        (60, "1 min 00 s"),
        (725, "12 min 05 s"),
        (3720, "1 h 02 min"),
        (7199.6, "2 h 00 min"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
