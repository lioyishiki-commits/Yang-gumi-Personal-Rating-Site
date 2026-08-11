# Yang-gumi release: 1.3.0
from __future__ import annotations

import pytest

import bangumi_archive
import bangumi_client as bgm


# Real NSFW subjects and cover URLs read from the user's authenticated Bangumi
# tag pages on 2026-08-09.  These are public subject metadata, not account data.
R18_COVER_SAMPLES = [
    (74467, "https://lain.bgm.tv/r/400/pic/cover/l/a5/39/74467_icWeq.jpg"),
    (8019, "https://lain.bgm.tv/r/400/pic/cover/l/08/b2/8019_6j4OL.jpg"),
    (34502, "https://lain.bgm.tv/r/400/pic/cover/l/f7/9d/34502_9GREd.jpg"),
    (9856, "https://lain.bgm.tv/r/400/pic/cover/l/3e/18/9856_25hoT.jpg"),
    (3206, "https://lain.bgm.tv/r/400/pic/cover/l/21/1e/3206_1A5jz.jpg"),
    (25469, "https://lain.bgm.tv/r/400/pic/cover/l/b5/75/25469_f0f6Z.jpg"),
    (44476, "https://lain.bgm.tv/r/400/pic/cover/l/40/d1/44476_lq7al.jpg"),
    (18022, "https://lain.bgm.tv/r/400/pic/cover/l/ae/b1/18022_2POSP.jpg"),
    (360361, "https://lain.bgm.tv/r/400/pic/cover/l/4d/45/360361_ezH24.jpg"),
    (145830, "https://lain.bgm.tv/r/400/pic/cover/l/07/5c/145830_bAzbs.jpg"),
    (38432, "https://lain.bgm.tv/r/400/pic/cover/l/ee/fe/38432_F8WzR.jpg"),
    (210268, "https://lain.bgm.tv/r/400/pic/cover/l/bf/53/210268_BlSB1.jpg"),
    (276175, "https://lain.bgm.tv/r/400/pic/cover/l/64/4b/276175_p8fcC.jpg"),
    (95609, "https://lain.bgm.tv/r/400/pic/cover/l/e3/b3/95609_C6MyU.jpg"),
    (62284, "https://lain.bgm.tv/r/400/pic/cover/l/ad/5f/62284_x8Xq2.jpg"),
    (43368, "https://lain.bgm.tv/r/400/pic/cover/l/6b/73/43368_OCpS9.jpg"),
    (283302, "https://lain.bgm.tv/r/400/pic/cover/l/fc/a5/283302_e56I1.jpg"),
    (321215, "https://lain.bgm.tv/r/400/pic/cover/l/5a/a4/321215_m6m2D.jpg"),
    (37994, "https://lain.bgm.tv/r/400/pic/cover/l/62/29/37994_1Td22.jpg"),
    (70280, "https://lain.bgm.tv/r/400/pic/cover/l/73/46/70280_T6uzq.jpg"),
    (37909, "https://lain.bgm.tv/r/400/pic/cover/l/f6/db/37909_Hh8hH.jpg"),
    (60329, "https://lain.bgm.tv/r/400/pic/cover/l/14/7a/60329_NBMf0.jpg"),
    (338672, "https://lain.bgm.tv/r/400/pic/cover/l/02/69/338672_lRD6h.jpg"),
    (532841, "https://lain.bgm.tv/r/400/pic/cover/l/81/de/532925_cf76Z.jpg"),
    (502213, "https://lain.bgm.tv/r/400/pic/cover/l/52/93/502213_Bp28v.jpg"),
    (376033, "https://lain.bgm.tv/r/400/pic/cover/l/de/ea/376033_KK4Dr.jpg"),
    (23080, "https://lain.bgm.tv/r/400/pic/cover/l/99/16/23080_w5GUW.jpg"),
    (18051, "https://lain.bgm.tv/r/400/pic/cover/l/dd/5f/18051_LUwWw.jpg"),
    (108554, "https://lain.bgm.tv/r/400/pic/cover/l/cd/e4/108554_vhVv7.jpg"),
    (323744, "https://lain.bgm.tv/r/400/pic/cover/l/81/53/323744_8299H.jpg"),
    (305740, "https://lain.bgm.tv/r/400/pic/cover/l/f9/6e/305740_ioKEi.jpg"),
    (165361, "https://lain.bgm.tv/r/400/pic/cover/l/99/2a/165361_3Xdyo.jpg"),
    (268790, "https://lain.bgm.tv/r/400/pic/cover/l/4a/15/268790_3clPP.jpg"),
    (132559, "https://lain.bgm.tv/r/400/pic/cover/l/72/a0/132559_e88xj.jpg"),
    (83727, "https://lain.bgm.tv/r/400/pic/cover/l/9a/08/83727_6RVP6.jpg"),
    (81862, "https://lain.bgm.tv/r/400/pic/cover/l/d9/93/81862_ZmVBM.jpg"),
    (67769, "https://lain.bgm.tv/r/400/pic/cover/l/f0/ca/67769_YY8Bw.jpg"),
    (46533, "https://lain.bgm.tv/r/400/pic/cover/l/5a/a2/46533_wNK71.jpg"),
    (286263, "https://lain.bgm.tv/r/400/pic/cover/l/55/00/286263_8pmkh.jpg"),
    (17652, "https://lain.bgm.tv/r/400/pic/cover/l/bb/43/17652_f2TTp.jpg"),
    (451561, "https://lain.bgm.tv/r/400/pic/cover/l/85/5c/451561_OlVnS.jpg"),
    (128257, "https://lain.bgm.tv/r/400/pic/cover/l/20/53/128257_Jn8wU.jpg"),
    (74466, "https://lain.bgm.tv/r/400/pic/cover/l/a4/54/74466_x6Z7D.jpg"),
    (96893, "https://lain.bgm.tv/r/400/pic/cover/l/31/bf/96893_iMLa8.jpg"),
    (108401, "https://lain.bgm.tv/r/400/pic/cover/l/b8/5b/108401_q1GVi.jpg"),
    (74457, "https://lain.bgm.tv/r/400/pic/cover/l/db/61/74457_LWW8b.jpg"),
    (62461, "https://lain.bgm.tv/r/400/pic/cover/l/fa/ae/62461_8deX1.jpg"),
    (184740, "https://lain.bgm.tv/r/400/pic/cover/l/b7/9c/184740_BNdiR.jpg"),
    (57919, "https://lain.bgm.tv/r/400/pic/cover/l/ea/e2/57919_KaIfQ.jpg"),
    (49816, "https://lain.bgm.tv/r/400/pic/cover/l/38/ac/49816_9UUJr.jpg"),
    (74468, "https://lain.bgm.tv/r/400/pic/cover/l/a1/80/74468_et9qT.jpg"),
    (66605, "https://lain.bgm.tv/r/400/pic/cover/l/4e/27/66605_3T5Nc.jpg"),
    (67985, "https://lain.bgm.tv/r/400/pic/cover/l/88/9f/67985_dUYTy.jpg"),
    (79938, "https://lain.bgm.tv/r/400/pic/cover/l/e5/61/79938_hbTf1.jpg"),
    (2107, "https://lain.bgm.tv/r/400/pic/cover/l/91/85/2107_DOJUX.jpg"),
    (62258, "https://lain.bgm.tv/r/400/pic/cover/l/ec/68/62258_e52qL.jpg"),
    (62507, "https://lain.bgm.tv/r/400/pic/cover/l/a2/f9/62507_Zyx8Y.jpg"),
    (18042, "https://lain.bgm.tv/r/400/pic/cover/l/7e/13/18042_MfmIX.jpg"),
    (62495, "https://lain.bgm.tv/r/400/pic/cover/l/3d/f4/62495_4j4EK.jpg"),
    (239280, "https://lain.bgm.tv/r/400/pic/cover/l/a6/cd/239280_xY4zA.jpg"),
    (331897, "https://lain.bgm.tv/r/400/pic/cover/l/bf/0c/331897_QZp30.jpg"),
    (332676, "https://lain.bgm.tv/r/400/pic/cover/l/7d/bd/332676_kK6nN.jpg"),
    (185974, "https://lain.bgm.tv/r/400/pic/cover/l/df/42/185974_7BmS9.jpg"),
    (264433, "https://lain.bgm.tv/r/400/pic/cover/l/70/01/264433_SIkve.jpg"),
    (60375, "https://lain.bgm.tv/r/400/pic/cover/l/c5/f7/60375_hXgFg.jpg"),
    (292093, "https://lain.bgm.tv/r/400/pic/cover/l/34/37/292093_Mejm5.jpg"),
    (8631, "https://lain.bgm.tv/r/400/pic/cover/l/47/38/8631_V08Y8.jpg"),
    (5961, "https://lain.bgm.tv/r/400/pic/cover/l/09/43/5961_m8hm9.jpg"),
    (60575, "https://lain.bgm.tv/r/400/pic/cover/l/cf/7a/60575_7ct13.jpg"),
    (37583, "https://lain.bgm.tv/r/400/pic/cover/l/e0/d9/37583_85di5.jpg"),
    (257852, "https://lain.bgm.tv/r/400/pic/cover/l/c5/6f/257852_YkxY5.jpg"),
    (278395, "https://lain.bgm.tv/r/400/pic/cover/l/db/4b/278395_ftO4t.jpg"),
    (294841, "https://lain.bgm.tv/r/400/pic/cover/l/eb/08/294841_F4J4N.jpg"),
    (287745, "https://lain.bgm.tv/r/400/pic/cover/l/6c/88/287745_SyBJb.jpg"),
    (237643, "https://lain.bgm.tv/r/400/pic/cover/l/76/c6/237643_6YKUS.jpg"),
    (237054, "https://lain.bgm.tv/r/400/pic/cover/l/97/f8/237054_NAPca.jpg"),
    (317600, "https://lain.bgm.tv/r/400/pic/cover/l/b7/37/317600_0k77o.jpg"),
    (207201, "https://lain.bgm.tv/r/400/pic/cover/l/ec/d3/207201_jp.jpg"),
    (313435, "https://lain.bgm.tv/r/400/pic/cover/l/7f/75/313435_hwqHm.jpg"),
    (192211, "https://lain.bgm.tv/r/400/pic/cover/l/c3/9c/192211_7fatI.jpg"),
    (306578, "https://lain.bgm.tv/r/400/pic/cover/l/6a/96/306578_IJAw7.jpg"),
    (92470, "https://lain.bgm.tv/r/400/pic/cover/l/2e/e1/92470_519dd.jpg"),
    (227166, "https://lain.bgm.tv/r/400/pic/cover/l/42/b9/227166_ApHbB.jpg"),
    (261860, "https://lain.bgm.tv/pic/cover/l/f4/b1/261860_q4RAZ.jpg"),
    (22290, "https://lain.bgm.tv/r/400/pic/cover/l/90/78/22290_1L2aJ.jpg"),
    (22423, "https://lain.bgm.tv/r/400/pic/cover/l/71/f7/22423_SksK2.jpg"),
    (4639, "https://lain.bgm.tv/r/400/pic/cover/l/33/b8/4639_kDq7d.jpg"),
    (347780, "https://lain.bgm.tv/r/400/pic/cover/l/c2/4d/347780_9v5R3.jpg"),
    (4347, "https://lain.bgm.tv/r/400/pic/cover/l/64/b3/4347_RrqdH.jpg"),
    (1795, "https://lain.bgm.tv/r/400/pic/cover/l/07/31/1795_CgxQU.jpg"),
    (88739, "https://lain.bgm.tv/r/400/pic/cover/l/16/18/88739_n4V88.jpg"),
    (38363, "https://lain.bgm.tv/r/400/pic/cover/l/9c/2a/38363_fwMcV.jpg"),
    (4066, "https://lain.bgm.tv/r/400/pic/cover/l/a1/98/4066_AI2T2.jpg"),
    (134929, "https://lain.bgm.tv/r/400/pic/cover/l/61/c7/134929_hBwAi.jpg"),
    (163041, "https://lain.bgm.tv/r/400/pic/cover/l/56/41/163041_I5Z65.jpg"),
    (2722, "https://lain.bgm.tv/r/400/pic/cover/l/33/26/2722_w40CX.jpg"),
    (13139, "https://lain.bgm.tv/r/400/pic/cover/l/ce/18/13139_cvHA4.jpg"),
    (611546, "https://lain.bgm.tv/r/400/pic/cover/l/c4/f2/611546_8pus3.jpg"),
]


@pytest.mark.parametrize(
    ("subject_id", "cover_url"),
    R18_COVER_SAMPLES,
    ids=[str(subject_id) for subject_id, _ in R18_COVER_SAMPLES],
)
def test_real_r18_archive_subject_uses_authenticated_cover(subject_id: int, cover_url: str) -> None:
    record = bangumi_archive.archive_subject(subject_id)
    assert record is not None
    assert record.get("nsfw") is True

    subject = bgm._archive_subject_dictionary(record, {"image": cover_url})

    assert subject["images"]["large"] == cover_url
    assert "no_icon_subject" not in cover_url


def test_authenticated_enrichment_fills_every_missing_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = dict(R18_COVER_SAMPLES)
    monkeypatch.setattr(bgm, "_cached_cover_url", lambda _subject_id: "")
    monkeypatch.setattr(bgm, "_remember_cover_urls", lambda _items: None)

    def fake_detail(subject_id: int, access_token: str) -> dict:
        assert access_token == "session-only-token"
        record = bangumi_archive.archive_subject(subject_id)
        assert record is not None
        detail = bgm._archive_subject_dictionary(record)
        detail["images"] = {"large": expected[subject_id]}
        return detail

    monkeypatch.setattr(bgm, "get_subject_with_access_token", fake_detail)
    source = [
        bgm._archive_subject_dictionary(bangumi_archive.archive_subject(subject_id) or {})
        for subject_id, _ in R18_COVER_SAMPLES
    ]

    enriched = bgm.enrich_authenticated_subjects(
        source, "session-only-token", max_workers=6,
    )

    assert len(enriched) == len(R18_COVER_SAMPLES)
    assert all(bgm._subject_cover_url(item) == expected[int(item["id"])] for item in enriched)
