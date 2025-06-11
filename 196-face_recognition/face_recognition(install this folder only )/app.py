# app.py
import os
import cv2
import numpy as np
from flask import Flask, render_template, request, redirect, url_for, g, Response, jsonify
from werkzeug.utils import secure_filename
import mediapipe as mp
import secrets
from database import get_db, register_user, get_registered_users, load_landmarks, get_user_id_by_name, close_connection, log_access_time, get_login_history
import logging
from flask import session
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['SECRET_KEY'] = secrets.token_hex(32)
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)

@app.teardown_appcontext
def teardown_db(exception=None):
    close_connection(exception)

mp_face_detection = mp.solutions.face_detection

def extract_face_landmarks(image):
    with mp_face_detection.FaceDetection(model_selection=0, min_detection_confidence=0.5) as face_detection:
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = face_detection.process(image_rgb)
        if results.detections:
            landmark_data = []
            for detection in results.detections:
                for i in range(6):
                    landmark = detection.location_data.relative_keypoints[i]
                    landmark_data.append((landmark.x, landmark.y))
            print("Extracted Landmarks:", landmark_data)  # Log extracted landmarks
            return landmark_data
        else:
            print("No landmarks detected")  # Log if no landmarks are detected
            return None

def compare_landmarks(live_landmarks, stored_landmarks, threshold=2.0):
    if len(live_landmarks) != len(stored_landmarks):
        print("Error: Number of landmarks does not match.")
        return None
    total_distance = sum(
        ((x1 - x2)**2 + (y1 - y2)**2)**0.5
        for (x1, y1), (x2, y2) in zip(live_landmarks, stored_landmarks)
    )
    print("Total Distance:", total_distance)
    return total_distance if total_distance < threshold else None

def normalize_landmarks(landmarks, width, height):
    return [(x / width, y / height) for x, y in landmarks]

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        national_id = request.form['national_id']
        image = request.files['image']

        if name.lower() == 'admin':  # Prevent registration with "admin" name
            return render_template('register.html', error="Cannot register with the name 'admin'.")

        if not all([name, national_id, image]):
            return render_template('register.html', error="All fields are required.")

        filename = secure_filename(image.filename)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)
        img = cv2.imread(image_path)
        landmarks = extract_face_landmarks(img)

        if landmarks is None:
            os.remove(image_path)
            return render_template('register.html', error="No face detected in the image.")

        success, error = register_user(name, national_id, landmarks)
        os.remove(image_path)

        if success:
            return redirect(url_for('login'))  # Redirect to login after successful registration
        else:
            return render_template('register.html', error=f"Registration failed: {error}")

    return render_template('register.html')  # Return the registration form for GET requests

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/')
def index():
    return render_template('index.html')  # Render the index page

@app.route('/user_details')
def user_details():
    name = request.args.get('name')
    national_id = request.args.get('national_id')

    if name == 'admin':
        login_history, error = get_login_history()
        if error:
            return "Error fetching login history", 500
        
        registered_users, error = get_registered_users()
        if error:
            return "Error fetching registered users", 500

        user = {'name': name, 'national_id': national_id}

        chart_data = [{'name': entry[0], 'time': entry[2].strftime('%Y-%m-%d %H:%M:%S')} for entry in login_history]

        return render_template('dashboard.html', user=user, login_history=login_history, chart_data=chart_data, registered_users=registered_users)
    elif name and national_id:
        user_id, error = get_user_id_by_name(name)
        if error:
            return "User not found", 404
        
        log_access_time(user_id)

        user = {'name': name, 'national_id': national_id}
        return render_template('details.html', user=user)
    else:
        return "Access Denied", 403

@app.route('/logout')
def logout():
    global recognized_user_name
    recognized_user_name = None  # Reset recognized user on logout
    session.clear()  # Clear session data
    return redirect(url_for('index'))  # Redirect to the index page

@app.route('/api/user_data')
def get_user_data():
    global recognized_user_name
    if recognized_user_name:
        user_id, error = get_user_id_by_name(recognized_user_name)
        if error:
            print("Error getting user ID:", error)  # Log error
            return jsonify({'error': error}), 404
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT name, national_id FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        if user:
            user_data = {'name': user[0], 'national_id': user[1]}
            print("User Data:", user_data)  # Log user data
            return jsonify(user_data)
        else:
            print("User not found in database")  # Log if user not found
            return jsonify({'error': 'User not found in database'}), 404
    else:
        print("No user recognized")  # Log if no user is recognized
        return jsonify({'error': 'No user recognized'}), 404
    
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

recognized_user_name = None  # Global variable to hold recognized name

def generate_frames():
    global recognized_user_name
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open camera")
        yield "Cannot open camera"
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Can't receive frame (stream end?). Exiting ...")
                break
            live_landmarks = extract_face_landmarks(frame)
            recognized_name = None  # Reset for each frame
            if live_landmarks:
                min_distance = float('inf')
                best_match_name = None
                with app.app_context():
                    db = get_db()
                    if db is None:
                        print("Failed to connect to the database.")
                        continue
                    cursor = db.cursor()
                    cursor.execute("SELECT id, name, national_id FROM users")
                    users = cursor.fetchall()
                    cursor.close()
                    for user_id, user_name, national_id in users:
                        stored_landmarks, error = load_landmarks(user_id)
                        if error:
                            print(f"Error loading landmarks for user {user_name}: {error}")
                            continue
                        if stored_landmarks:
                            stored_landmarks = [(x, y) for x, y in stored_landmarks]
                            distance = compare_landmarks(live_landmarks, stored_landmarks)
                            if distance is not None and distance < min_distance:
                                min_distance = distance
                                best_match_name = user_name
                if best_match_name:
                    recognized_name = best_match_name
                    recognized_user_name = best_match_name  # Update global variable
                    print(f"Recognized: {recognized_name} (Distance: {min_distance})")
                else:
                    recognized_user_name = None  # Clear the name if no user is recognized
            else:
                recognized_user_name = None  # Reset recognized user if no landmarks are detected
            _, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    finally:
        cap.release()

if __name__ == '__main__':
    app.run(debug=True)