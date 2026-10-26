# Karofi Water Purifier cho Home Assistant

**[🇺🇸 English](README.md) | 🇻🇳 Tiếng Việt**

**Karofi Water Purifier** là tiện ích tích hợp cục bộ qua Bluetooth Low Energy (BLE) dành cho các dòng máy lọc nước thông minh Karofi trong Home Assistant. Tiện ích cho phép theo dõi chỉ số chất rắn hòa tan (TDS) trong nước tinh khiết theo thời gian thực, đánh giá chất lượng nước uống, theo dõi hạn sử dụng của hệ thống đa lõi lọc (5 đến 12+ lõi) theo tỷ lệ phần trăm, số ngày còn lại theo lịch và số giờ sử dụng thực tế còn lại, đồng thời cho phép đặt lại hoặc điều chỉnh chu kỳ thay lõi trực tiếp trên giao diện Home Assistant mà không cần kết nối đám mây hay máy chủ bên ngoài.

---

## Mục lục

- [Tính năng nổi bật](#tính-năng-nổi-bật)
- [Khả năng tương thích phần cứng](#khả-năng-tương-thích-phần-cứng)
- [Danh sách thực thể (Entities)](#danh-sách-thực-thể-entities)
- [Mã lỗi thiết bị](#mã-lỗi-thiết-bị)
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
- **Cường độ tín hiệu Bluetooth**: Hiển thị độ mạnh tín hiệu Bluetooth của máy theo đơn vị dBm, giúp bạn kiểm tra vùng phủ sóng và tìm vị trí phù hợp cho Bluetooth Proxy.
- **Phân loại chất lượng nước theo chuẩn an toàn**: Tự động đánh giá độ tinh khiết của nước thành các mức rõ ràng:
  - **Tốt** (< 50 ppm): Nước có độ tinh khiết cao, đạt chuẩn uống trực tiếp.
  - **Khá** (50-150 ppm): Nước ở mức an toàn; nên theo dõi sự thay đổi theo thời gian.
  - **Kém** (> 150 ppm): Nước có nguy cơ suy giảm độ tinh khiết; nên kiểm tra màng RO hoặc thay lõi lọc.
  - **Không xác định**: Đang chờ dữ liệu đo hoặc cảm biến tạm thời chưa khả dụng.
- **Theo dõi linh hoạt hệ thống nhiều lõi lọc (5, 7, 8, 9, 10, 11, 12+ lõi)**:
  - Tự động nhận diện và theo dõi số lượng lõi lọc thực tế từ dữ liệu BLE của thiết bị.
  - Tên gọi đơn giản, nhất quán (`Lõi 1`, `Lõi 2`, `Lõi 3`, ...) phù hợp cho mọi dòng máy mà không bị gán cứng sai loại lõi.
- **Ba chỉ số vòng đời cho từng lõi**: Mỗi cấp lõi lọc cung cấp cảm biến tỷ lệ phần trăm còn lại (0% đến 100%), cảm biến số ngày còn lại theo lịch và cảm biến số giờ sử dụng thực tế còn lại, giúp theo dõi trực quan trên thẻ giao diện và thiết lập tự động hóa.
- **Thao tác bảo trì linh hoạt**:
  - **Nút đặt lại một chạm**: Khi thay lõi thực tế, nhấn nút trên Home Assistant để gửi lệnh đặt lại đến máy; số ngày còn lại sẽ cập nhật theo dữ liệu máy phản hồi.
- **Cảnh báo lỗi dễ hiểu**: Khi máy lọc nước gặp sự cố, cảm biến Sự cố sẽ bật. Bạn mở cảm biến để xem mã lỗi và lời giải thích ngắn gọn bằng ngôn ngữ Home Assistant của bạn.
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

| Nền tảng (Platform) | Mã định danh                                                            | Đơn vị / Kiểu  | Mô tả                                                                                  |
| :------------------ | :---------------------------------------------------------------------- | :------------- | :------------------------------------------------------------------------------------- |
| **Sensor**          | `sensor.<device_name>_pure_water_tds` (mẫu Entity ID)                   | `ppm`          | Chỉ số TDS nước tinh khiết theo thời gian thực.                                        |
| **Sensor**          | `sensor.<device_name>_water_quality` (mẫu Entity ID)                    | `enum`         | Xếp hạng chất lượng nước (`good`, `fair`, `poor`, `unknown`).                          |
| **Sensor**          | `sensor.<device_name>_signal_strength` (mẫu Entity ID)                  | `dBm`          | Cường độ tín hiệu Bluetooth (dBm), đọc từ quảng cáo của thiết bị. Cảm biến chẩn đoán.  |
| **Sensor**          | `sensor.<device_name>_filter_<n>_life` (mẫu Entity ID)                  | `%`            | Phần trăm thời gian còn lại của Lõi `<n>` (thêm tự động).                              |
| **Sensor**          | `sensor.<device_name>_filter_<n>_days_remaining` (mẫu Entity ID)        | `ngày`         | Số ngày còn lại theo lịch đến hạn thay Lõi `<n>`.                                      |
| **Sensor**          | `sensor.<device_name>_filter_<n>_usage_hours_remaining` (mẫu Entity ID) | `giờ`          | Số giờ sử dụng thực tế còn lại của Lõi `<n>`.                                          |
| **Binary Sensor**   | `binary_sensor.<device_name>_connectivity` (mẫu Entity ID)              | `connectivity` | Trạng thái kết nối Bluetooth với máy lọc nước. Cảm biến chẩn đoán.                     |
| **Binary Sensor**   | `binary_sensor.<device_name>_problem` (mẫu Entity ID)                   | `problem`      | Bật khi máy lọc nước báo sự cố. Thuộc tính hiển thị mã lỗi và lời giải thích ngắn gọn. |
| **Button**          | `button.<device_name>_reset_filter_<n>` (mẫu Entity ID)                 | `restore`      | Gửi lệnh đặt lại cho cấp lõi lọc tương ứng.                                            |

---

## Mã lỗi thiết bị

Khi máy gặp sự cố, máy sẽ gửi mã lỗi. Cảm biến **Sự cố** sẽ bật và phần thuộc tính hiển thị mã lỗi kèm lời giải thích ngắn gọn bằng ngôn ngữ Home Assistant của bạn.

|  Mã | Ý nghĩa                         |
| --: | :------------------------------ |
|   1 | Bơm đã chạy liên tục hơn 5 giờ. |
|   2 | Nước cấp vào máy yếu.           |
|   4 | Không có nước cấp vào máy.      |
|   5 | Máy có thể đang bị rò rỉ nước.  |
| 100 | Chỉ số TDS của nước đã lọc cao. |

Khi mọi thứ hoạt động bình thường, cảm biến sẽ tắt. Nếu máy mất kết nối, cảm biến sẽ tạm thời hiển thị là không khả dụng cho đến khi kết nối lại. Nếu bạn thấy mã lỗi không có trong bảng, Home Assistant vẫn sẽ hiển thị `Mã lỗi thiết bị không xác định <mã>` bằng ngôn ngữ của bạn. Nếu sự cố vẫn tiếp diễn, hãy liên hệ Karofi để được hỗ trợ.

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

Các giá trị thời gian của lõi lọc tính bằng giờ:

- **Số ngày còn lại theo lịch** = `max(0, round(iro_lTime / 24))`
- **Số ngày tuổi thọ theo lịch** = `max(1, round(iro_hsd / 24))`
- **Số giờ sử dụng thực tế còn lại** = `iro_rtr`
- **Số giờ sử dụng thực tế định mức** = `iro_rtt`

Tỷ lệ phần trăm theo lịch sử dụng `iro_lTime` và `iro_hsd`; ngày thay lõi đã lưu chỉ dùng làm giá trị dự phòng khi chưa có dữ liệu thời gian còn lại từ thiết bị. Tỷ lệ phần trăm theo thời gian sử dụng thực tế sử dụng `iro_rtr` và `iro_rtt`. Tuổi thọ hiệu dụng là giá trị nhỏ hơn giữa hai tỷ lệ, vì vậy việc sử dụng nhiều có thể rút ngắn tuổi thọ hiệu dụng ngay cả khi thời gian theo lịch vẫn còn.

$$\text{Tỷ lệ theo lịch} = \max\left(0, \min\left(100, \text{làm tròn}\left(\frac{\text{Số giờ còn lại theo lịch}}{\text{Số giờ tuổi thọ theo lịch}} \times 100\right)\right)\right)$$

$$\text{Tỷ lệ sử dụng thực tế} = \max\left(0, \min\left(100, \text{làm tròn}\left(\frac{\text{Số giờ sử dụng còn lại}}{\text{Số giờ sử dụng định mức}} \times 100\right)\right)\right)$$

$$\text{Tuổi thọ hiệu dụng} = \min\left(\text{Tỷ lệ theo lịch}, \text{Tỷ lệ sử dụng thực tế}\right)$$

Cảm biến số ngày còn lại vẫn dựa trên lịch. Các cảm biến còn lại hiển thị dung lượng định mức qua thuộc tính: `capacity_days` trên cảm biến số ngày, `capacity_hours` trên cảm biến số giờ sử dụng, và cả hai dung lượng cùng `calendar_percent`, `runtime_percent` trên cảm biến tuổi thọ.

> [!NOTE]
> Tỷ lệ tuổi thọ hiệu dụng hiện xem xét cả thời gian theo lịch và thời gian sử dụng thực tế. Đây là thay đổi hành vi đối với người dùng sử dụng nhiều, vì tỷ lệ có thể giảm nhanh hơn trước. Một số dòng máy không báo cáo dữ liệu sử dụng thực tế; khi đó cảm biến tuổi thọ sẽ tự động quay về tuổi thọ theo lịch mà không gây lỗi.

<!-- MD028/no-blanks-blockquote: Blank line inside blockquote -->

> [!NOTE]
> Cảm biến lõi lọc hiển thị thuộc tính `last_seen` và `stale`. `last_seen` là thời điểm dữ liệu động gần nhất của lõi lọc (số giờ còn lại theo lịch hoặc theo thời gian sử dụng). Trước khi một lõi lọc gửi dữ liệu động, các cảm biến vẫn khả dụng khi máy lọc đang kết nối và sử dụng giá trị dựa trên lịch. Sau khi lõi lọc đã gửi dữ liệu động, nếu không có dữ liệu động mới trong hơn một giờ, các cảm biến tương ứng sẽ chuyển sang trạng thái không khả dụng thay vì tiếp tục hiển thị giá trị cũ. Các nút đặt lại sẽ không khả dụng khi máy lọc mất kết nối.

### Khi thay lõi lọc mới

1. Truy cập trang thiết bị Karofi tại **Cài đặt** > **Thiết bị & Dịch vụ** > **Karofi**.
2. Trong khu vực các nút bấm, chọn **Nhấn** tại nút đặt lại tương ứng (ví dụ: `Đặt lại lõi 1`).
3. Tiện ích gửi lệnh đặt lại đến máy. Sau khi `async_reset_filter` thành công, giá trị đặt lại cục bộ vẫn chờ khi dữ liệu thiết bị còn cũ hoặc chưa xác nhận việc đặt lại; dữ liệu đã xác nhận từ máy sẽ trở thành nguồn chính.

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
