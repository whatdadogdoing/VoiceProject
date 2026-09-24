import random
import re

from services.redis_client import get_redis

PHRASE_HISTORY_SIZE = 10
PHRASE_HISTORY_TTL_SECONDS = 7 * 24 * 60 * 60
# The record of which phrases a user has already been given this cycle. It has to
# outlive long gaps between logins, or waiting would reset the protection.
PHRASE_USED_TTL_SECONDS = 365 * 24 * 60 * 60

PHRASES = [
    "Hôm nay trời nắng đẹp và gió mát.",
    "Tôi thích uống cà phê vào buổi sáng.",
    "Bạn có thể chỉ đường giúp tôi được không.",
    "Cuối tuần này gia đình tôi sẽ đi du lịch.",
    "Chiếc xe màu đỏ đang đậu trước cổng.",
    "Cô ấy đang học nấu ăn món Việt Nam.",
    "Chúng ta nên đi ngủ sớm để giữ sức khỏe.",
    "Anh trai tôi làm việc ở một công ty công nghệ.",
    "Mùa thu lá cây chuyển sang màu vàng.",
    "Tôi vừa mua một cuốn sách rất hay.",
    "Buổi tối nay chúng tôi sẽ xem phim cùng nhau.",
    "Cửa hàng đó bán rất nhiều loại trái cây tươi.",
    "Con mèo nhà tôi rất thích nằm phơi nắng.",
    "Hãy nhớ mang theo ô khi trời có mưa.",
    "Tôi đang tập thể dục mỗi buổi sáng sớm.",
    "Chợ hoa ngày Tết lúc nào cũng đông vui.",
    "Anh ấy vừa hoàn thành xong bài tập về nhà.",
    "Chúng tôi đã đặt vé máy bay đi Đà Nẵng.",
    "Món phở bò là món ăn tôi thích nhất.",
    "Bạn nên uống đủ nước mỗi ngày.",
    "Trường học của tôi nằm gần một công viên lớn.",
    "Chiếc điện thoại này có camera rất nét.",
    "Tôi vừa nhận được email quan trọng từ công ty.",
    "Hôm qua chúng tôi đi xem một buổi hòa nhạc.",
    "Cô giáo đang giảng bài rất nhiệt tình.",
    "Chúng ta hãy cùng nhau dọn dẹp nhà cửa.",
    "Bố tôi thích đọc báo vào mỗi sáng.",
    "Con đường này thường xuyên bị kẹt xe giờ cao điểm.",
    "Tôi đã đặt lịch hẹn khám bác sĩ vào thứ ba.",
    "Chiếc bánh sinh nhật trông thật đẹp mắt.",
    "Chúng tôi vừa chuyển đến một căn hộ mới.",
    "Bạn có muốn đi dạo công viên buổi chiều không.",
    "Cửa sổ phòng tôi nhìn ra một hồ nước xanh.",
    "Tôi rất thích nghe nhạc khi lái xe.",
    "Đội bóng của trường vừa giành chức vô địch.",
    "Chị tôi đang chuẩn bị cho kỳ thi cuối kỳ.",
    "Chúng tôi sẽ gặp nhau tại quán cà phê quen thuộc.",
    "Ánh nắng buổi sớm chiếu qua khung cửa sổ.",
    "Tôi cần mua thêm một số đồ dùng học tập.",
    "Bà tôi kể chuyện cổ tích rất hay.",
    "Chiếc thuyền nhỏ lướt nhẹ trên mặt hồ.",
    "Tôi đang học cách chơi đàn guitar.",
    "Cả nhà cùng nhau quây quần bên mâm cơm tối.",
    "Chúng tôi vừa hoàn thành dự án đúng thời hạn.",
    "Bạn nên đội mũ bảo hiểm khi đi xe máy.",
    "Cơn mưa rào bất chợt làm ướt cả con phố.",
    "Tôi thích ngắm hoàng hôn trên bãi biển.",
    "Chiếc laptop của tôi vừa được nâng cấp bộ nhớ.",
    "Chúng ta nên tiết kiệm điện và nước mỗi ngày.",
    "Cô bạn thân của tôi vừa chuyển đến thành phố khác.",
    "Buổi họp lớp năm nay được tổ chức ở quê.",
    "Tôi vừa học được một công thức nấu ăn mới.",
    "Chiếc cầu này bắc qua một con sông lớn.",
    "Chúng tôi thường đi chợ vào mỗi sáng chủ nhật.",
    "Bạn có thể giúp tôi kiểm tra lại email này không.",
    "Ánh đèn thành phố về đêm thật lung linh.",
    "Tôi đang cố gắng học thêm một ngoại ngữ mới.",
    "Chiếc balo này rất tiện lợi khi đi du lịch.",
    "Chúng tôi sẽ tổ chức tiệc mừng sinh nhật cuối tuần.",
    "Cơn gió mùa đông bắc tràn về khiến trời se lạnh.",
    "Tôi thường nghe podcast trong lúc đi bộ.",
    "Chiếc đồng hồ đeo tay này là quà của bố tôi.",
    "Chúng ta hãy lên kế hoạch cho chuyến đi sắp tới.",
    "Bạn nhớ tắt đèn trước khi ra khỏi phòng nhé.",
    "Tôi vừa hoàn thành khóa học lập trình cơ bản.",
    "Chiếc xe đạp của tôi bị hỏng bánh sau.",
    "Chúng tôi đã tham quan bảo tàng lịch sử thành phố.",
    "Bạn có biết quán ăn nào ngon gần đây không.",
    "Ánh trăng rằm tháng tám sáng vằng vặc trên bầu trời.",
    "Tôi đang tìm một công việc bán thời gian.",
    "Chiếc túi xách này được làm từ da thật.",
    "Chúng ta nên phân loại rác trước khi vứt đi.",
    "Bạn có thể nói chậm lại một chút được không.",
    "Buổi sáng nay tôi dậy sớm để chạy bộ.",
    "Tôi rất thích không khí se lạnh của mùa đông.",
    "Chiếc máy tính bảng này rất phù hợp để học tập.",
    "Chúng tôi vừa ký hợp đồng thuê nhà mới.",
    "Bạn nhớ khóa cửa cẩn thận trước khi đi ngủ.",
    "Cơn sóng lớn đánh vào bờ biển rất mạnh.",
    "Tôi đang lên danh sách những việc cần làm hôm nay.",
    "Chiếc áo khoác này giữ ấm rất tốt vào mùa đông.",
    "Chúng ta hãy cùng nhau trồng thêm cây xanh.",
    "Bạn có thể gửi lại tài liệu cho tôi không.",
    "Buổi tối hôm qua trời mưa rất to.",
    "Tôi thích đọc sách trước khi đi ngủ mỗi tối.",
    "Chiếc bàn làm việc của tôi luôn gọn gàng ngăn nắp.",
    "Chúng tôi đã đặt bàn trước tại nhà hàng đó.",
    "Bạn nhớ mang theo giấy tờ tùy thân khi đi xa.",
    "Cơn nắng gắt buổi trưa khiến ai cũng mệt mỏi.",
    "Tôi vừa tải xong bộ phim yêu thích về máy.",
    "Chiếc vali này rất nhẹ và dễ mang theo.",
    "Chúng ta nên uống một ly nước ấm mỗi sáng.",
    "Bạn có thể bật đèn giúp tôi được không.",
    "Buổi biểu diễn âm nhạc tối nay rất đặc sắc.",
    "Tôi đang chuẩn bị hồ sơ xin việc mới.",
    "Chiếc ghế sofa này rất êm và thoải mái.",
    "Chúng tôi thường đi bộ quanh hồ vào mỗi buổi chiều.",
    "Bạn nhớ kiểm tra lại vé trước khi lên tàu.",
    "Cơn bão vừa đi qua khiến nhiều cây đổ.",
    "Tôi thích trồng hoa trong khu vườn nhỏ của mình.",
    "Chiếc micro này thu âm rất rõ và trong.",
    "Chúng ta hãy thử món ăn mới ở quán này xem sao.",
]

PHRASE_MATCH_THRESHOLD = 0.6


def random_phrases(count: int) -> list[str]:
    return random.sample(PHRASES, count)


def another_phrase(exclude: list[str]) -> str:
    candidates = [p for p in PHRASES if p not in exclude]
    return random.choice(candidates) if candidates else random.choice(PHRASES)


async def next_verify_phrase(user_id: str) -> str:
    """Pick a verify phrase this user has not been given yet in the current cycle.

    A recording of an earlier challenge is genuine speech, so neither the speaker
    match nor the anti-spoofing model can reject it; the only thing that stops it
    being replayed is that the new challenge is a different sentence. Excluding
    only the last few phrases (the first version) let a phrase come back after
    about a dozen logins, which a test demonstrates. Now a phrase is not issued
    again until the whole pool has been used once, so a captured recording only
    becomes useful again after roughly PHRASES-many logins. When the pool runs
    out a new cycle starts, but the last PHRASE_HISTORY_SIZE phrases are carried
    over so none of them can come straight back.

    This narrows the window a lot but does not close it: a phrase does eventually
    recur, and word-level splicing from several recordings is not addressed by it.
    """
    redis_client = get_redis()
    history_key = f"phrase_history:{user_id}"
    used_key = f"phrase_used:{user_id}"

    raw_history = await redis_client.lrange(history_key, 0, PHRASE_HISTORY_SIZE - 1)
    history = [h.decode() for h in raw_history]
    used = {m.decode() for m in await redis_client.smembers(used_key)}

    candidates = [p for p in PHRASES if p not in used]
    if not candidates:
        # pool exhausted: start over, keeping the most recent phrases off the table
        await redis_client.delete(used_key)
        used = set(history)
        if used:
            await redis_client.sadd(used_key, *used)
        candidates = [p for p in PHRASES if p not in used]

    phrase = random.choice(candidates)

    await redis_client.sadd(used_key, phrase)
    await redis_client.expire(used_key, PHRASE_USED_TTL_SECONDS)
    await redis_client.lpush(history_key, phrase)
    await redis_client.ltrim(history_key, 0, PHRASE_HISTORY_SIZE - 1)
    await redis_client.expire(history_key, PHRASE_HISTORY_TTL_SECONDS)
    return phrase


def _normalize_words(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[.,!?;:\"']", "", text)
    return [w for w in text.split() if w]


def matches_phrase(expected: str, transcript: str) -> bool:
    """`transcript` must come from server-side STT (services/stt.py)
    run on the actual submitted audio -- never from a client-supplied field,
    which a caller could simply leave empty to skip this check entirely."""
    expected_words = _normalize_words(expected)
    transcript_words = set(_normalize_words(transcript))
    if not expected_words:
        return True
    matched = sum(1 for w in expected_words if w in transcript_words)
    return (matched / len(expected_words)) >= PHRASE_MATCH_THRESHOLD
