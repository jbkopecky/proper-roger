"""Speed, tone and filter limits shared by every part of the trainer."""


WPM_MIN, WPM_MAX = 5, 60
TONE_MIN, TONE_MAX = 100, 3000
FIST_MAX = 50           # % timing variation
VARY_MAX = 10           # WPM
VARY_TONE_HZ = 60       # tone spread between stations when --vary is on
FILTER_MIN, FILTER_MAX = 100, 1000   # Hz

SAMPLE_RATE = 16000
