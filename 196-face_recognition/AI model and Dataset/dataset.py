import pyodbc
from flask import g, current_app
from datetime import datetime

SERVER = 'DESKTOP-48VUG0Q'  # Your server name
DATABASE = 'face_detection'  # Your database name

def get_db():
    """Connects to the SQL Server database using a trusted connection."""
    db = getattr(g, '_database', None)
    if db is None:
        try:
            # Construct the connection string using pyodbc
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"  # Or the appropriate driver
                f"SERVER={SERVER};"
                f"DATABASE={DATABASE};"
                f"Trusted_Connection=yes;"
            )
            db = g._database = pyodbc.connect(conn_str)
        except pyodbc.Error as ex:
            sqlstate = ex.args[0]
            if current_app:
                current_app.logger.error(f"Database connection error: {ex}")
            else:
                print(f"Database connection error (outside app context): {ex}") # Fallback logging
            return None  # Return None if connection fails
    return db

def close_connection(e=None):  # Accept exception argument
    """Closes the database connection at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def register_user(name, national_id, landmarks):
    """Registers a new user with face landmarks."""
    db = get_db()
    if db is None:
        return False, "Database connection failed"  # Handle connection failure
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, national_id) VALUES (?, ?)",
            (name, national_id)
        )
        db.commit()  # Commit the changes
        cursor.execute("SELECT @@IDENTITY")  # Get the last inserted ID (SQL Server)
        user_id = cursor.fetchone()[0]
        for i, (x, y) in enumerate(landmarks):
            cursor.execute(
                "INSERT INTO landmarks (user_id, landmark_id, x, y) VALUES (?, ?, ?, ?)",
                (user_id, i, x, y)
            )
        db.commit()
        return True, None  # Return success and no error
    except pyodbc.Error as e:
        db.rollback()  # Rollback on error
        return False, str(e)  # Return failure and the error message
    finally:
        cursor.close()  # Ensure the cursor is closed

def load_landmarks(user_id):
    """Loads landmarks for a given user ID."""
    db = get_db()
    if db is None:
        return None, "Database connection failed"
    cursor = db.cursor()
    try:
        cursor.execute("SELECT x, y FROM landmarks WHERE user_id = ? ORDER BY landmark_id", (user_id,))
        landmarks = cursor.fetchall()  # Fetch all landmarks as (x, y) tuples
        return landmarks, None  # Return landmarks and no error
    except pyodbc.Error as e:
        return None, str(e)  # Return None and the error message
    finally:
        cursor.close()

def get_registered_users():
    """Fetches all registered users excluding the admin."""
    db = get_db()
    if db is None:
        return None, "Database connection failed"
    cursor = db.cursor()
    try:
        cursor.execute("SELECT name, national_id FROM users WHERE name != 'admin'")
        users = cursor.fetchall()
        return users, None
    except pyodbc.Error as e:
        return None, str(e)
    finally:
        cursor.close()

def log_access_time(user_id):
    """Logs the access time for a user."""
    db = get_db()
    if db is None:
        return False, "Database connection failed"
    cursor = db.cursor()
    try:
        access_time = datetime.now()
        cursor.execute(
            "INSERT INTO access_logs (user_id, access_time) VALUES (?, ?)",
            (user_id, access_time)
        )
        db.commit()
        return True, None
    except pyodbc.Error as e:
        db.rollback()
        return False, str(e)
    finally:
        cursor.close()

def get_login_history():
    """Fetches the login history for all users."""
    db = get_db()
    if db is None:
        return None, "Database connection failed"
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT u.name, u.national_id, a.access_time 
            FROM access_logs a
            JOIN users u ON a.user_id = u.id
            ORDER BY a.access_time DESC
        """)
        history = cursor.fetchall()
        return history, None
    except pyodbc.Error as e:
        return None, str(e)
    finally:
        cursor.close()

def get_user_id_by_name(name):
    """Gets the user ID by name."""
    db = get_db()
    if db is None:
        return None, "Database connection failed"
    cursor = db.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE name = ?", (name,))
        result = cursor.fetchone()
        if result:
            return result[0], None  # Return the user ID and no error
        else:
            return None, "User not found"  # Return None and an error message
    except pyodbc.Error as e:
        return None, str(e)  # Return None and the error message
    finally:
        cursor.close()