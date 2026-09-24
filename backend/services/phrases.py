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
    "Hôm qua chúng tôi đi xem một buổi hòa nhạc.",
    "Cô giáo đang giảng bài rất nhiệt tình.",
    "Chúng ta hãy cùng nhau dọn dẹp nhà cửa.",
    "Bố tôi thích đọc báo vào mỗi sáng.",
    "Con đường này thường xuyên bị kẹt xe giờ cao điểm.",
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
    "Cả nhà cùng nhau quây quần bên mâm cơm tối.",
    "Chúng tôi vừa hoàn thành dự án đúng thời hạn.",
    "Bạn nên đội mũ bảo hiểm khi đi xe máy.",
    "Cơn mưa rào bất chợt làm ướt cả con phố.",
    "Tôi thích ngắm hoàng hôn trên bãi biển.",
    "Chúng ta nên tiết kiệm điện và nước mỗi ngày.",
    "Cô bạn thân của tôi vừa chuyển đến thành phố khác.",
    "Buổi họp lớp năm nay được tổ chức ở quê.",
    "Tôi vừa học được một công thức nấu ăn mới.",
    "Chiếc cầu này bắc qua một con sông lớn.",
    "Chúng tôi thường đi chợ vào mỗi sáng chủ nhật.",
    "Tôi đang cố gắng học thêm một ngoại ngữ mới.",
    "Chúng tôi sẽ tổ chức tiệc mừng sinh nhật cuối tuần.",
    "Cơn gió mùa đông bắc tràn về khiến trời se lạnh.",
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
    "Chúng ta nên uống một ly nước ấm mỗi sáng.",
    "Bạn có thể bật đèn giúp tôi được không.",
    "Buổi biểu diễn âm nhạc tối nay rất đặc sắc.",
    "Tôi đang chuẩn bị hồ sơ xin việc mới.",
    "Chúng tôi thường đi bộ quanh hồ vào mỗi buổi chiều.",
    "Bạn nhớ kiểm tra lại vé trước khi lên tàu.",
    "Cơn bão vừa đi qua khiến nhiều cây đổ.",
    "Tôi thích trồng hoa trong khu vườn nhỏ của mình.",
    "Buổi sáng em bé thức dậy rất sớm.",
    "Mẹ tôi nấu canh chua cho cả nhà.",
    "Con chim nhỏ đậu trên cành cây xoài.",
    "Chiều nay chúng tôi sẽ đi thăm ông bà.",
    "Dòng sông chảy chậm qua cánh đồng lúa.",
    "Ngôi nhà của bà nằm cuối con hẻm nhỏ.",
    "Hôm qua tôi đã gặp lại người bạn cũ.",
    "Cô giáo dặn cả lớp làm bài đầy đủ.",
    "Trời đã tối nhưng đường phố vẫn còn đông.",
    "Anh ấy đang sửa lại chiếc xe đạp cũ.",
    "Bà ngoại kể chuyện cho các cháu nghe.",
    "Cơn gió mát thổi qua khung cửa sổ.",
    "Chợ sáng bán đủ các loại rau xanh.",
    "Tôi muốn học nấu món canh bí đao.",
    "Những đám mây trắng trôi chậm trên trời.",
    "Ba mẹ tôi thường dậy sớm tập thể dục.",
    "Em gái tôi rất thích vẽ tranh.",
    "Ngày mai trời có thể sẽ mưa to.",
    "Chúng tôi ngồi uống trà bên hiên nhà.",
    "Cậu bé chạy nhanh đuổi theo con diều.",
    "Quán cơm gần trường luôn rất đông khách.",
    "Tôi thích ăn bánh mì với trứng chiên.",
    "Người thợ đang sơn lại bức tường trắng.",
    "Vườn nhà tôi có nhiều hoa hồng đỏ.",
    "Cả gia đình cùng đi dạo bên hồ.",
    "Chú tôi làm nghề lái xe đường dài.",
    "Cô ấy mặc chiếc áo dài màu xanh.",
    "Tôi thường đọc sách trước khi đi ngủ.",
    "Con chó nhỏ nằm ngủ dưới gốc cây.",
    "Buổi tối cả nhà quây quần bên bếp lửa.",
    "Anh trai tôi đang chuẩn bị đi làm.",
    "Bác nông dân ra đồng từ rất sớm.",
    "Hôm nay tôi được nghỉ nên ở nhà.",
    "Chúng tôi mua ít trái cây cho ngày lễ.",
    "Ông ngoại thích ngồi câu cá bên sông.",
    "Mẹ dặn tôi nhớ mặc thêm áo ấm.",
    "Tiếng mưa rơi trên mái nhà nghe thật êm.",
    "Người bán hàng mỉm cười chào khách.",
    "Chị tôi đang học làm bánh ngọt.",
    "Trên bàn có một bình hoa cúc vàng.",
    "Cậu học trò đọc bài thật to và rõ.",
    "Ngọn núi phía xa phủ đầy sương mù.",
    "Tôi thích đi bộ dọc theo bờ biển.",
    "Thầy giáo giải thích bài toán rất dễ hiểu.",
    "Chúng ta nên giữ gìn môi trường sống.",
    "Cửa hiệu bên đường bán nhiều loại bánh.",
    "Cô hàng xóm thường mang rau sang cho tôi.",
    "Buổi chiều tôi hay ra sân tưới cây.",
    "Đứa bé cười khúc khích khi được bế lên.",
    "Chuyến tàu đến ga đúng giờ mỗi sáng.",
    "Tôi đã học được nhiều điều từ thầy cô.",
    "Đường về nhà hôm nay khá vắng vẻ.",
    "Chúng tôi trò chuyện đến tận khuya.",
    "Nhà tôi ở gần một khu chợ lớn.",
    "Mùa đông người ta thường mặc áo len.",
    "Anh ấy kể một câu chuyện rất vui.",
    "Tôi thích nghe tiếng chim hót buổi sáng.",
    "Ông bà tôi sống ở một làng nhỏ.",
    "Cô y tá nhẹ nhàng chăm sóc bệnh nhân.",
    "Cả lớp cùng hát một bài hát vui.",
    "Bạn tôi đang tìm một căn nhà mới.",
    "Trời chuyển lạnh nên tôi mang thêm khăn.",
    "Tôi ăn sáng bằng một bát cháo nóng.",
    "Đàn cá bơi lội tung tăng trong hồ.",
    "Bố tôi thích uống trà xanh buổi tối.",
    "Cây cầu mới nối hai bờ dòng sông.",
    "Cô bé đội chiếc mũ len màu đỏ.",
    "Tôi hay đi chợ cùng mẹ vào cuối tuần.",
    "Ngày hội của làng diễn ra rất vui vẻ.",
    "Người lái đò chèo thuyền qua sông.",
    "Chúng tôi trồng thêm vài cây ăn quả.",
    "Anh bảo vệ mở cổng cho mọi người.",
    "Bà hàng nước ngồi dưới bóng cây đa.",
    "Tôi cần đi khám răng vào tuần sau.",
    "Cả nhà tôi rất thích ăn cá kho.",
    "Cô ấy làm việc ở một bệnh viện lớn.",
    "Sáng nay trời trong xanh và đầy nắng.",
    "Em tôi đang tập viết chữ đẹp.",
    "Người bạn thân luôn ở bên khi tôi buồn.",
    "Đêm qua tôi ngủ rất ngon giấc.",
    "Chúng tôi cùng nhau dọn dẹp sân trường.",
    "Bác tài xế lái xe rất cẩn thận.",
    "Chiếc đồng hồ trên tường chạy rất đúng.",
    "Trẻ em thích chơi đùa trong công viên.",
    "Con đường làng được trải nhựa mới.",
    "Tôi nhờ bạn mang giúp cuốn sách này.",
    "Mưa tạnh rồi và bầu trời sáng trở lại.",
    "Bà cụ chậm rãi đi qua con ngõ nhỏ.",
    "Chúng tôi chia nhau phần bánh còn lại.",
    "Tôi thấy rất vui khi được gặp lại bạn.",
    "Cửa sổ phòng tôi hướng ra vườn cây.",
    "Cô giáo khen bạn ấy học rất chăm.",
    "Trái xoài chín vàng thơm cả gian bếp.",
    "Người thợ mộc đang làm một chiếc bàn.",
    "Buổi trưa nắng gắt nên ai cũng nghỉ ngơi.",
    "Tôi mong ngày mai trời sẽ đẹp.",
    "Mẹ đang gói bánh chưng cho ngày Tết.",
    "Ông tôi đọc báo và uống trà mỗi sáng.",
    "Chiếc thuyền nhỏ neo lại bên bến sông.",
    "Đàn trâu về chuồng khi trời sắp tối.",
    "Người ta cấy lúa vào đầu mùa mưa.",
    "Tôi rất quý những buổi sáng yên tĩnh.",
    "Cả xóm cùng nhau góp tiền làm đường.",
    "Bà bán rau ngồi ở góc chợ quen.",
    "Chị ấy cười rất tươi khi nhận quà.",
    "Cây phượng bên sân trường nở hoa đỏ rực.",
    "Chúng tôi cùng chờ tàu ở nhà ga.",
    "Em bé thích nghe mẹ hát ru.",
    "Sau cơn mưa không khí trở nên mát dịu.",
    "Ông chủ quán luôn niềm nở với khách.",
    "Tôi nhớ hương vị món ăn của bà.",
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
