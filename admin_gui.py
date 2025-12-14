import tkinter as tk
from tkinter import ttk
import sqlite3
import os

DB_FILE = "user_progress.db"

def fetch_data():
    """Fetches data from the user_progress database."""
    if not os.path.exists(DB_FILE):
        return []
        
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Ensure the table and all columns exist before querying
        cursor.execute("PRAGMA table_info(user_progress)")
        columns = [info[1] for info in cursor.fetchall()]
        if 'first_name' not in columns:
            # Handle old schema gracefully if needed, or just fail
            conn.close()
            return [("Database schema is old.", "Please delete user_progress.db and let the bot recreate it.", "", "", "")]
            
        cursor.execute("SELECT user_id, first_name, difficulty, current_question, score FROM user_progress ORDER BY user_id")
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"Database error: {e}")
        return []

def populate_tree(tree):
    """Clears and populates the treeview with data."""
    # Clear existing data
    for i in tree.get_children():
        tree.delete(i)
    
    # Fetch and insert new data
    data = fetch_data()
    for row in data:
        tree.insert("", "end", values=row)

def main():
    root = tk.Tk()
    root.title("عرض تقدم مستخدمي البوت")
    root.geometry("800x400")

    # Style
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", 
        background="#D3D3D3",
        foreground="black",
        rowheight=25,
        fieldbackground="#D3D3D3"
    )
    style.map('Treeview', background=[('selected', '#347083')])

    # Frame for Treeview
    tree_frame = tk.Frame(root)
    tree_frame.pack(pady=10, padx=10, fill="both", expand=True)

    # Scrollbar
    tree_scroll = tk.Scrollbar(tree_frame)
    tree_scroll.pack(side="right", fill="y")

    # Treeview
    tree = ttk.Treeview(tree_frame, yscrollcommand=tree_scroll.set, selectmode="extended")
    tree.pack(fill="both", expand=True)
    tree_scroll.config(command=tree.yview)

    # Define columns
    tree['columns'] = ("User ID", "First Name", "Difficulty", "Question #", "Score")

    # Format columns
    tree.column("#0", width=0, stretch=tk.NO)
    tree.column("User ID", anchor=tk.W, width=120)
    tree.column("First Name", anchor=tk.W, width=150)
    tree.column("Difficulty", anchor=tk.CENTER, width=100)
    tree.column("Question #", anchor=tk.CENTER, width=100)
    tree.column("Score", anchor=tk.CENTER, width=100)

    # Create headings
    tree.heading("#0", text="", anchor=tk.W)
    tree.heading("User ID", text="معرف المستخدم", anchor=tk.W)
    tree.heading("First Name", text="الاسم الأول", anchor=tk.W)
    tree.heading("Difficulty", text="المستوى", anchor=tk.CENTER)
    tree.heading("Question #", text="رقم السؤال الحالي", anchor=tk.CENTER)
    tree.heading("Score", text="النتيجة", anchor=tk.CENTER)

    # Refresh Button
    refresh_button = tk.Button(root, text="🔄 تحديث البيانات", command=lambda: populate_tree(tree))
    refresh_button.pack(pady=10)

    # Initial data load
    populate_tree(tree)

    root.mainloop()

if __name__ == "__main__":
    main()
