import datetime
import pytz
from main import is_weekday


def test_is_weekday_true_for_wednesday():
    tz = pytz.timezone("America/Vancouver")
    wed = tz.localize(datetime.datetime(2026, 7, 22, 6, 0))   # a Wednesday
    assert is_weekday(wed) is True


def test_is_weekday_false_for_saturday():
    tz = pytz.timezone("America/Vancouver")
    sat = tz.localize(datetime.datetime(2026, 7, 25, 6, 0))   # a Saturday
    assert is_weekday(sat) is False
