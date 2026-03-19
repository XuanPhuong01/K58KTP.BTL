# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, redirect, url_for, session
import pyodbc

app = Flask(__name__)
app.secret_key = "secret_key_ban_sach_ai_bookstore"

# =============================
# 1. KẾT NỐI SQL SERVER
# =============================
def get_connection():
    # Đảm bảo DATABASE tên là 'bansachonline' giống trong máy bạn
    conn = pyodbc.connect(
        "DRIVER={SQL Server};"
        "SERVER=localhost;"
        "DATABASE=bansachonline;"
        "Trusted_Connection=yes;"
    )
    return conn

def fetch_all_as_dict(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

# =============================
# 2. TRANG CHỦ & TÌM KIẾM THÔNG MINH
# =============================
@app.route("/")
def index():
    conn = get_connection()
    cursor = conn.cursor()

    keyword = request.args.get("keyword", "").strip()
    ma_danh_muc = request.args.get("danh_muc")

    # SQL nâng cấp: Tìm theo Tên sách, Tác giả HOẶC Tên danh mục (Lĩnh vực)
    sql = """
        SELECT s.*, d.ten_danh_muc 
        FROM sach s
        LEFT JOIN danh_muc d ON s.ma_danh_muc = d.ma_danh_muc
        WHERE 1=1
    """
    params = []

    if keyword:
        sql += " AND (s.ten_sach LIKE ? OR s.tac_gia LIKE ? OR d.ten_danh_muc LIKE ?)"
        search_param = f'%{keyword}%'
        params.extend([search_param, search_param, search_param])

    if ma_danh_muc:
        sql += " AND s.ma_danh_muc = ?"
        params.append(ma_danh_muc)

    cursor.execute(sql, params)
    books = fetch_all_as_dict(cursor)

    # Lấy danh sách danh mục để hiện thanh menu
    cursor.execute("SELECT * FROM danh_muc")
    categories = fetch_all_as_dict(cursor)

    # --- LOGIC GỢI Ý CÁ NHÂN HÓA (AI CONTENT-BASED) ---
    recommended_books = []
    if "user_id" in session:
        # Lấy danh mục mà người dùng tương tác gần nhất
        sql_goi_y = """
            SELECT TOP 1 s.ma_danh_muc 
            FROM lich_su_tuong_tac ls
            JOIN sach s ON ls.ma_sach = s.ma_sach
            WHERE ls.ma_nguoi_dung = ?
            ORDER BY ls.thoi_gian DESC
        """
        cursor.execute(sql_goi_y, (session["user_id"],))
        res = cursor.fetchone()
        if res:
            cursor.execute("SELECT TOP 4 * FROM sach WHERE ma_danh_muc = ?", (res[0],))
            recommended_books = fetch_all_as_dict(cursor)

    # Nếu chưa đăng nhập hoặc chưa có lịch sử, gợi ý sách mới nhất
    if not recommended_books:
        cursor.execute("SELECT TOP 4 * FROM sach ORDER BY ma_sach DESC")
        recommended_books = fetch_all_as_dict(cursor)

    conn.close()
    return render_template("index.html", books=books, categories=categories, recommended_books=recommended_books)

# =============================
# 3. CHI TIẾT SÁCH & GHI LẠI TƯƠNG TÁC
# =============================
@app.route("/chitiet/<int:ma_sach>")
def chitiet(ma_sach):
    conn = get_connection()
    cursor = conn.cursor()

    # Lưu lịch sử tương tác để AI học (nếu đã đăng nhập)
    if "user_id" in session:
        try:
            cursor.execute("INSERT INTO lich_su_tuong_tac (ma_nguoi_dung, ma_sach, thoi_gian) VALUES (?, ?, GETDATE())", 
                           (session["user_id"], ma_sach))
            conn.commit()
        except:
            pass 

    cursor.execute("SELECT * FROM sach WHERE ma_sach=?", (ma_sach,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Sách không tồn tại"

    columns = [column[0] for column in cursor.description]
    book = dict(zip(columns, row))

    cursor.execute("""
        SELECT dg.*, nd.ho_ten FROM danh_gia dg
        JOIN nguoi_dung nd ON dg.ma_nguoi_dung = nd.ma_nguoi_dung 
        WHERE dg.ma_sach = ? ORDER BY dg.ngay_binh_luan DESC
    """, (ma_sach,))
    reviews = fetch_all_as_dict(cursor)

    conn.close()
    return render_template("chitiet.html", book=book, reviews=reviews)

# =============================
# 4. THANH TOÁN & TRỪ TỒN KHO
# =============================
@app.route("/xac-nhan-don-hang", methods=["POST"])
def xac_nhan_don_hang():
    if "user_id" not in session or not session.get("giohang"):
        return redirect(url_for("index"))

    sdt = request.form.get("sdt")
    dia_chi = request.form.get("dia_chi")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 1. Tính tổng tiền
        tong_bill = 0
        for item in session["giohang"]:
            cursor.execute("SELECT gia FROM sach WHERE ma_sach=?", (item["ma_sach"],))
            tong_bill += cursor.fetchone()[0] * item["so_luong"]

        # 2. Tạo đơn hàng
        cursor.execute("""
            INSERT INTO don_hang (ma_nguoi_dung, ngay_dat, trang_thai, dia_chi_giao, so_dien_thoai, tong_tien)
            OUTPUT INSERTED.ma_don_hang
            VALUES (?, GETDATE(), N'Chờ xử lý', ?, ?, ?)
        """, (session["user_id"], dia_chi, sdt, tong_bill))
        ma_don_hang = cursor.fetchone()[0]

        # 3. Lưu chi tiết & Cập nhật Tồn kho/Đã bán (Quan trọng)
        for item in session["giohang"]:
            cursor.execute("SELECT gia FROM sach WHERE ma_sach=?", (item["ma_sach"],))
            gia_hien_tai = cursor.fetchone()[0]

            # Lưu chi tiết đơn
            cursor.execute("INSERT INTO chi_tiet_don_hang (ma_don_hang, ma_sach, so_luong, gia) VALUES (?, ?, ?, ?)",
                           (ma_don_hang, item["ma_sach"], item["so_luong"], gia_hien_tai))
            
            # CẬP NHẬT KHO: Giảm tồn kho, tăng số lượng đã bán
            cursor.execute("""
                UPDATE sach 
                SET so_luong_ton = so_luong_ton - ?, 
                    so_luong_da_ban = ISNULL(so_luong_da_ban, 0) + ? 
                WHERE ma_sach = ?
            """, (item["so_luong"], item["so_luong"], item["ma_sach"]))

        conn.commit()
        session.pop("giohang")
        return render_template("thanhcong.html", ma_don=ma_don_hang)

    except Exception as e:
        conn.rollback()
        return f"Lỗi: {str(e)}"
    finally:
        conn.close()

# --- GIỮ NGUYÊN CÁC PHẦN LOGIN, GIỎ HÀNG, ADMIN CÒN LẠI ---
# (Thêm các hàm them_gio, giohang, login, logout... từ code cũ của bạn vào đây)

@app.route("/them_gio/<int:ma_sach>")
def them_gio(ma_sach):
    if "giohang" not in session: session["giohang"] = []
    giohang = session["giohang"]
    found = False
    for item in giohang:
        if item["ma_sach"] == ma_sach:
            item["so_luong"] += 1
            found = True
            break
    if not found: giohang.append({"ma_sach": ma_sach, "so_luong": 1})
    session["giohang"] = giohang
    session.modified = True
    return redirect(url_for("giohang"))

@app.route("/giohang")
def giohang():
    conn = get_connection(); cursor = conn.cursor()
    cart_items = []; tong_tien = 0
    if "giohang" in session:
        for item in session["giohang"]:
            cursor.execute("SELECT ma_sach, ten_sach, gia, hinh_anh FROM sach WHERE ma_sach=?", (item["ma_sach"],))
            book = cursor.fetchone()
            if book:
                thanh_tien = book[2] * item["so_luong"]
                cart_items.append({"ma_sach": book[0], "ten_sach": book[1], "gia": book[2], "so_luong": item["so_luong"], "hinh_anh": book[3], "thanh_tien": thanh_tien})
                tong_tien += thanh_tien
    conn.close()
    return render_template("giohang.html", cart_items=cart_items, tong_tien=tong_tien)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email"); mat_khau = request.form.get("mat_khau")
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM nguoi_dung WHERE email=? AND mat_khau=?", (email, mat_khau))
        user_row = cursor.fetchone()
        if user_row:
            session["user_id"] = user_row[0]; session["user_name"] = user_row[1]; session["user_role"] = user_row[4]
            conn.close(); return redirect(url_for("index"))
        conn.close(); return render_template("dangnhap.html", error="Sai tài khoản!")
    return render_template("dangnhap.html")
@app.route("/capnhat/<int:ma_sach>", methods=["POST"])
def capnhat(ma_sach):
    so_luong = int(request.form.get("so_luong", 1))
    if "giohang" in session:
        giohang = session["giohang"]
        for item in giohang:
            if item["ma_sach"] == ma_sach:
                item["so_luong"] = so_luong
                break
        session["giohang"] = giohang
        session.modified = True
    return redirect(url_for("giohang"))

@app.route("/xoa/<int:ma_sach>")
def xoa_sach(ma_sach):
    if "giohang" in session:
        session["giohang"] = [item for item in session["giohang"] if item["ma_sach"] != ma_sach]
        session.modified = True
    return redirect(url_for("giohang"))
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
@app.route("/thanhtoan")
def thanhtoan():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    if not session.get("giohang"):
        return redirect(url_for("giohang"))

    conn = get_connection()
    cursor = conn.cursor()
    
    tong_tien = 0
    # Tinh lai tong tien tu Database cho chinh xac
    for item in session["giohang"]:
        cursor.execute("SELECT gia FROM sach WHERE ma_sach=?", (item["ma_sach"],))
        res = cursor.fetchone()
        if res:
            tong_tien += res[0] * item["so_luong"]
            
    conn.close()
    return render_template("thanhtoan.html", tong_tien=tong_tien)
if __name__ == "__main__":
    app.run(debug=True)
