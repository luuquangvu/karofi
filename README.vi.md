# Karofi Water Purifier cho Home Assistant

**[🇺🇸 English](README.md) | 🇻🇳 Tiếng Việt**

**Karofi Water Purifier** là tiện ích tích hợp cục bộ qua Bluetooth Low Energy (BLE) dành cho các dòng máy lọc nước thông minh Karofi trong Home Assistant. Tiện ích cho phép theo dõi chỉ số chất rắn hòa tan (TDS) trong nước tinh khiết theo thời gian thực, đánh giá chất lượng nước uống, theo dõi hạn sử dụng của hệ thống đa lõi lọc (5 đến 12+ lõi) theo tỷ lệ phần trăm và số ngày còn lại, đồng thời cho phép đặt lại hoặc điều chỉnh chu kỳ thay lõi trực tiếp trên giao diện Home Assistant mà không cần kết nối đám mây hay máy chủ bên ngoài.

---

## Mục lục

- [Tính năng nổi bật](#tính-năng-nổi-bật)
- [Khả năng tương thích phần cứng](#khả-năng-tương-thích-phần-cứng)
- [Danh sách thực thể (Entities)](#danh-sách-thực-thể-entities)
- [Cài đặt](#cài-đặt)
  - [Cách 1: Cài đặt qua HACS (Khuyến nghị)](#cách-1-cài-đặt-qua-hacs-khuyến-nghị)
  - [Cách 2: Cài đặt thủ công](#cách-2-cài-đặt-thủ-công)
- [Thiết lập và Cấu hình](#thiết-lập-và-cấu-hình)
- [Quản lý vòng đời lõi lọc](#quản-lý-vòng-đời-lõi-lọc)
- [Ví dụ Tự động hóa (Automations)](#ví-dụ-tự-động-hóa-automations)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Chất lượng mã nguồn và Độ tin cậy](#chất-lượng-mã-nguồn-và-độ-tin-cậy)
- [Đóng góp](#đóng-góp)
- [Giấy phép](#giấy-phép)
- [Hỗ trợ dự án](#hỗ-trợ-dự-án)

---

## Tính năng nổi bật

- **Kết nối cục bộ 100% (Không cần Cloud)**: Hoạt động hoàn toàn qua kết nối Bluetooth Low Energy (BLE) thông qua ngăn xếp Bluetooth tích hợp sẵn của Home Assistant. Không yêu cầu hub chuyên dụng, không cần tài khoản đám mây hay kết nối internet.
- **Giám sát chỉ số TDS nước tinh khiết thời gian thực**: Đo chính xác nồng độ tổng chất rắn hòa tan (đơn vị ppm) sau màng lọc RO trực tiếp từ cảm biến tích hợp của máy.
- **Phân loại chất lượng nước theo chuẩn an toàn**: Tự động đánh giá độ tinh khiết của nước thành các mức rõ ràng:
  - **Tốt** (< 50 ppm): Nước có độ tinh khiết cao, đạt chuẩn uống trực tiếp.
  - **Khá** (50-150 ppm): Nước ở mức an toàn; nên theo dõi sự thay đổi theo thời gian.
  - **Kém** (> 150 ppm): Nước có nguy cơ suy giảm độ tinh khiết; nên kiểm tra màng RO hoặc thay lõi lọc.
  - **Không xác định**: Đang chờ dữ liệu đo hoặc cảm biến tạm thời chưa khả dụng.
- **Theo dõi linh hoạt hệ thống nhiều lõi lọc (5, 7, 8, 9, 10, 11, 12+ lõi)**:
  - Tự động nhận diện và theo dõi số lượng lõi lọc thực tế từ dữ liệu BLE của thiết bị.
  - Tên gọi đơn giản, nhất quán (`Lõi 1`, `Lõi 2`, `Lõi 3`, ...) phù hợp cho mọi dòng máy mà không bị gán cứng sai loại lõi.
- **Cơ chế hiển thị kép cho từng lõi**: Mỗi cấp lõi lọc cung cấp song song cảm biến tỷ lệ phần trăm còn lại (0% đến 100%) và cảm biến số ngày còn lại, giúp theo dõi trực quan trên thẻ giao diện và thiết lập tự động hóa.
- **Thao tác bảo trì linh hoạt**:
  - **Nút đặt lại một chạm**: Khi thay lõi thực tế, nhấn nút trên Home Assistant để gửi lệnh đặt lại đến máy; số ngày còn lại sẽ cập nhật theo dữ liệu máy phản hồi.
- **Lưu trữ dữ liệu cục bộ bền vững**: Mốc thời gian thay lõi và dữ liệu bảo trì được lưu an toàn trong hệ thống lưu trữ của Home Assistant (`.storage`), đảm bảo không bị thất lạc khi khởi động lại hệ thống hoặc khởi động lại máy chủ.
- **Tương thích Bluetooth Proxy và ESPHome**: Hoạt động ổn định với các bộ thu phát Bluetooth Proxy (ESPHome hoặc Shelly BLE Proxy), giúp nhận dữ liệu tin cậy ngay cả khi máy lọc nước đặt cách xa máy chủ Home Assistant.
- **Xử lý kết nối BLE tin cậy**: Sử dụng thư viện `bleak-retry-connector` cùng cơ chế phân tích luồng JSON đa khung (multi-frame stream), ghi lệnh dự phòng không chờ phản hồi (write-without-response) và cơ chế kiểm soát thời gian chờ để duy trì kết nối ổn định trong môi trường tần số 2.4 GHz nhiều can nhiễu.
- **Giao diện song ngữ**: Tích hợp sẵn ngôn ngữ tiếng Anh (`en`) và tiếng Việt (`vi`).

---

## Khả năng tương thích phần cứng

Tiện ích này tương thích với các dòng máy lọc nước thông minh Karofi sử dụng bo mạch điều khiển Bluetooth Low Energy (BLE) của Makipos:

- **Tên thiết bị nhận diện Bluetooth**: Phát sóng với tiền tố `TCM-*` (ví dụ: `TCM-112233`).
- **UUID dịch vụ Bluetooth**: `000012ac-0000-1000-8000-00805f9b34fb`.
- **Các model tương thích**: Các dòng máy lọc nước Karofi có kết nối Bluetooth và các mẫu máy sử dụng bo mạch điều khiển Makipos tương thích.

---

## Danh sách thực thể (Entities)

| Nền tảng (Platform) | Mã thực thể (Entity ID)             | Đơn vị / Kiểu  | Mô tả                                                                    |
| :------------------ | :---------------------------------- | :------------- | :----------------------------------------------------------------------- |
| **Sensor**          | `sensor.<mac>_tds`                  | `ppm`          | Chỉ số TDS nước tinh khiết theo thời gian thực.                          |
| **Sensor**          | `sensor.<mac>_quality`              | `enum`         | Xếp hạng chất lượng nước (`good`, `fair`, `poor`, `unknown`).            |
| **Sensor**          | `sensor.<mac>_filter_<n>_percent`   | `%`            | Phần trăm thời gian còn lại của Lõi `<n>` (thêm tự động).                |
| **Sensor**          | `<mac>_filter_<n>_days` (unique ID) | `ngày`         | Số ngày còn lại đến hạn thay Lõi `<n>`; `_days` là hậu tố của unique ID. |
| **Binary Sensor**   | `binary_sensor.<mac>_connectivity`  | `connectivity` | Trạng thái kết nối Bluetooth với máy lọc nước.                           |
| **Button**          | `button.<mac>_btn_reset_filter_<n>` | `restore`      | Gửi lệnh đặt lại cho cấp lõi lọc tương ứng.                              |

---

## Cài đặt

### Cách 1: Cài đặt qua HACS (Khuyến nghị)

[![Thêm Karofi vào HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=luuquangvu&repository=karofi&category=integration)

1. Mở **HACS** trên Home Assistant.
2. Bấm vào biểu tượng ba chấm ở góc trên bên phải và chọn **Custom repositories**.
3. Thêm URL kho lưu trữ: `https://github.com/luuquangvu/karofi` với danh mục (Category) là **Integration**.
4. Tìm kiếm **Karofi Water Purifier** và bấm **Download**.
5. Khởi động lại Home Assistant.

### Cách 2: Cài đặt thủ công

1. Tải bản phát hành mới nhất từ mục [Releases](https://github.com/luuquangvu/karofi/releases).
2. Giải nén tệp tin và sao chép thư mục `custom_components/karofi` vào thư mục `<config_dir>/custom_components/` của Home Assistant.
3. Khởi động lại Home Assistant.

---

## Thiết lập và Cấu hình

1. Trong Home Assistant, đi tới **Cài đặt** > **Thiết bị & Dịch vụ**.
2. Nếu máy lọc nước Karofi đang bật và nằm trong vùng phủ sóng Bluetooth của máy chủ hoặc ESPHome Bluetooth Proxy, hệ thống sẽ **tự động phát hiện**. Bạn chỉ cần bấm **Cấu hình** trên thẻ thiết bị được tìm thấy.
3. Nếu thiết bị không hiển thị tự động, bấm **Thêm tích hợp** và tìm kiếm từ khóa **Karofi**.
4. Chọn máy lọc nước của bạn từ danh sách thiết bị tìm thấy, hoặc chọn **"Nhập địa chỉ MAC thủ công..."** để điền địa chỉ MAC Bluetooth (ví dụ: `AA:BB:CC:11:22:33`).
5. Xác nhận thiết lập. Các thực thể tương ứng sẽ được tự động tạo và gán vào thiết bị.

> [!NOTE]
> Sau khi thiết lập, dữ liệu lõi lọc có thể cần vài lần quét nền để ổn định. Số liệu ban đầu có thể chưa đầy đủ hoặc chưa chính xác trong khi các cấp lõi lọc và trạng thái thiết bị đang được phát hiện; hãy đợi vài phút để các giá trị cập nhật.

---

## Quản lý vòng đời lõi lọc

Hai trường thiết bị (`iro_rtr` và `iro_lTime`) đều có đơn vị giờ. Số ngày còn lại được tính bằng `max(0, round(iro_rtr / 24))`; tuổi thọ định mức theo ngày được tính bằng `max(1, round(iro_lTime / 24))`. Tỷ lệ phần trăm dùng hai giá trị theo ngày này. Ngày thay lõi đã lưu chỉ được dùng làm giá trị dự phòng khi chưa có dữ liệu thời gian còn lại từ thiết bị.

$$\text{Tỷ lệ phần trăm còn lại} = \max\left(0, \min\left(100, \text{làm tròn}\left(\frac{\text{Số ngày còn lại}}{\text{Tuổi thọ tối đa}} \times 100\right)\right)\right)$$

### Khi thay lõi lọc mới

1. Truy cập trang thiết bị Karofi tại **Cài đặt** > **Thiết bị & Dịch vụ** > **Karofi**.
2. Trong khu vực các nút bấm, chọn **Nhấn** tại nút đặt lại tương ứng (ví dụ: `Đặt lại lõi 1`).
3. Tiện ích gửi lệnh đặt lại đến máy. Cảm biến số ngày còn lại sẽ cập nhật theo dữ liệu thiết bị báo.

### Khi đồng bộ lõi lọc đang sử dụng

Số ngày còn lại được đọc từ máy lọc và không thể chỉnh sửa qua tiện ích. Nếu giá trị không đúng, hãy đặt lại lõi tương ứng trên máy rồi chờ dữ liệu cập nhật; có thể dùng nút đặt lại trong Home Assistant khi thay lõi.

---

## Ví dụ Tự động hóa (Automations)

Gửi thông báo đến điện thoại khi chất lượng nước TDS ở mức kém, hoặc tự động theo dõi toàn bộ các lõi lọc được phát hiện và gửi cảnh báo khi có bất kỳ lõi nào còn dưới 15 ngày sử dụng:

```yaml
automation:
  - id: karofi_filter_replacement_alert
    alias: "Karofi: Cảnh báo cần thay lõi lọc"
    # Chạy hàng ngày để kiểm tra nếu có bất kỳ cấp lõi lọc nào (5 đến 12+) còn dưới 15 ngày sử dụng
    trigger:
      - platform: time
        at: "09:00:00"
    condition:
      - condition: template
        value_template: >-
          {{ integration_entities('karofi')
            | select('match', '^sensor\.')
            | expand
            | selectattr('attributes.unit_of_measurement', 'eq', 'd')
            | selectattr('state', 'is_number')
            | map(attribute='state')
            | map('int')
            | select('lt', 15)
            | list | count > 0 }}
    action:
      - action: notify.notify
        data:
          title: "Nhắc nhở thay lõi lọc nước Karofi"
          message: >-
            Các lõi lọc sau đây sắp hết hạn sử dụng (dưới 15 ngày):
            {% for item in integration_entities('karofi')
               | select('match', '^sensor\.')
               | expand
               | selectattr('attributes.unit_of_measurement', 'eq', 'd')
               | selectattr('state', 'is_number') %}
              {% if item.state | int < 15 %}
              - {{ item.name }}: chỉ còn {{ item.state }} ngày
              {% endif %}
            {% endfor %}
            Vui lòng chuẩn bị sẵn lõi lọc thay thế.

  - id: karofi_high_tds_alert
    alias: "Karofi: Cảnh báo TDS nước cao"
    trigger:
      - platform: numeric_state
        entity_id: sensor.karofi_water_purifier_pure_water_tds
        above: 150
        for:
          minutes: 2
    action:
      - action: notify.notify
        data:
          title: "Cảnh báo chất lượng nước"
          message: "Chỉ số TDS nước tinh khiết hiện tại là {{ trigger.to_state.state }} ppm (Ngưỡng Kém). Vui lòng kiểm tra màng lọc RO và đường cấp nước."
```

---

## Yêu cầu hệ thống

- **Home Assistant**: Phiên bản `2026.10.0` trở lên.
- **Bluetooth**: Bộ điều hợp Bluetooth cục bộ hoặc bộ chuyển tiếp [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html) / Shelly Bluetooth Gateway.
- **Python**: `>= 3.14` (Được Home Assistant quản lý tự động).

---

## Chất lượng mã nguồn và Độ tin cậy

Để duy trì tiêu chuẩn cao về độ tin cậy và an toàn, dự án sử dụng bộ công cụ phát triển và bảo mật tự động hiện đại:

- **Đánh giá Pull Request tự động**: Sử dụng [CodeRabbit AI](https://coderabbit.ai) để phân tích chi tiết các thay đổi, giúp phát hiện sớm các lỗi logic và trường hợp biên trước khi phát hành.
- **Tối ưu hóa mã nguồn**: [Sourcery AI](https://sourcery.ai) liên tục rà soát mã nguồn để đề xuất các cấu trúc Python sạch, hiệu quả và chuẩn mực hơn.
- **Phân tích tĩnh & Bảo mật**: [CodeQL](https://codeql.github.com) thực hiện quét chuyên sâu để nhận diện các rủi ro bảo mật tiềm ẩn, đảm bảo mã nguồn tuân thủ các quy chuẩn an toàn.
- **Quy trình phát triển chặt chẽ**:
  - **[Ruff](https://github.com/astral-sh/ruff)**: Kiểm tra lỗi và định dạng mã cực nhanh, giúp code luôn nhất quán.
  - **[Ty](https://github.com/astral-sh/ty)** & **[Pyright](https://github.com/Microsoft/pyright)**: Kiểm tra kiểu dữ liệu hai lớp để phát hiện lỗi kiểu trước khi chạy (runtime) và đảm bảo tính ổn định của API.
  - **[Pytest](https://github.com/pytest-dev/pytest)**: Hệ thống kiểm thử tự động đảm bảo các tính năng luôn vận hành ổn định.
  - **[Interrogate](https://github.com/econchick/interrogate)**: Bắt buộc viết chú thích (docstring) đầy đủ cho tất cả các hàm và lớp để code luôn dễ đọc, dễ hiểu.
  - **[Prettier](https://github.com/prettier/prettier)**: Duy trì định dạng nhất quán cho các tệp tài liệu và cấu hình.

> [!NOTE]
> Mọi kết quả từ các công cụ tự động đều được quản trị viên dự án trực tiếp rà soát và xác nhận kỹ lưỡng, đảm bảo sự ổn định cao nhất cho người dùng.

---

## Đóng góp

Mọi đóng góp nhằm cải thiện và phát triển dự án đều được hoan nghênh:

1. Xem danh sách các vấn đề hiện có hoặc [mở một Issue mới](https://github.com/luuquangvu/karofi/issues).
2. Khi đóng góp mã nguồn, vui lòng fork kho lưu trữ, tạo nhánh phát triển và gửi Pull Request. Hãy đảm bảo toàn bộ quy trình kiểm định vượt qua bằng lệnh `uv run --locked tools/validate.py`.

---

## Giấy phép

Dự án được phân phối theo giấy phép **MIT License**. Xem chi tiết tại tệp [LICENSE](LICENSE).

---

## Hỗ trợ dự án

Nếu tiện ích này hữu ích cho ngôi nhà thông minh của bạn, hãy cân nhắc ủng hộ để duy trì và phát triển dự án:

[![Donate qua PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/luuquangvu89)
