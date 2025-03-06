from flask import Flask, jsonify, request
import os
import calendar
import logging
import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta
from urllib.parse import urlparse

app = Flask(__name__)

@app.route('/')
def home():
    return "Welcome to HavenLedger!"


# --------------------------------- Database Connection --------------------------------- #

# TODO: Replace the hard-coded credentials with environment variables
def get_db_connection():
    connection = None
    try:
        # Heroku JawsDB MySQL connection using environment variable
        jawsdb_url = urlparse(os.environ['JAWSDB_URL'])
        # Heroku JawsDB MySQL connection
        connection = mysql.connector.connect(
            user=jawsdb_url.username,
            password=jawsdb_url.password,
            host=jawsdb_url.hostname,
            database=jawsdb_url.path[1:],
            port=jawsdb_url.port
        )
    except Error as err:
        print(f"Error: '{err}'")
    return connection


# ---------------------- Fetch Room Details ---------------------- #
@app.route('/api/get_room_details', methods=['GET'])
def get_room_details():
    """Fetch room details and dynamically determine if a room is Private or Semi-Private"""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    query = """
        SELECT f.facility_name, r.room_number, 
               (SELECT COUNT(*) FROM residents WHERE residents.room_id = r.room_id) AS resident_count
        FROM rooms r
        JOIN facilities f ON r.facility_id = f.facility_id
        ORDER BY f.facility_name, r.room_number;
    """
    cursor.execute(query)
    rooms = cursor.fetchall()
    cursor.close()
    connection.close()

    # Structure the data
    room_details = {}
    for room in rooms:
        facility = room["facility_name"]
        if facility not in room_details:
            room_details[facility] = []
        
        # Determine room type dynamically
        if room["resident_count"] == 0:
            room_type = "Vacant"
        elif room["resident_count"] == 1:
            room_type = "Private"
        else:
            room_type = "Semi-Private"

        room_details[facility].append({
            "room": room["room_number"],
            "room_type": room_type,
            "status": "Occupied" if room["resident_count"] > 0 else "Vacant"
        })

    return jsonify(room_details)


# ---------------------- Fetch Room Occupancy ---------------------- #
@app.route('/api/get_room_occupancy', methods=['GET'])
def get_room_occupancy():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    query = """
        SELECT f.facility_name, r.room_number, res.name AS resident, 
               res.payment_amount AS amount, res.payment_due_date AS date, 
               p.status AS payment_status
        FROM residents res
        JOIN rooms r ON res.room_id = r.room_id
        JOIN facilities f ON r.facility_id = f.facility_id
        LEFT JOIN payments p ON res.resident_id = p.resident_id
        ORDER BY f.facility_name, r.room_number, res.name;
    """
    cursor.execute(query)
    occupants = cursor.fetchall()
    cursor.close()
    connection.close()

    # Structure the data by facility
    room_occupancy = {}
    room_resident_count = {}  # To track number of residents per room

    for occ in occupants:
        facility = occ["facility_name"]
        room_number = occ["room_number"]

        if facility not in room_occupancy:
            room_occupancy[facility] = []

        if (facility, room_number) not in room_resident_count:
            room_resident_count[(facility, room_number)] = 0

        room_resident_count[(facility, room_number)] += 1  # Count residents

        room_occupancy[facility].append({
            "room": room_number,
            "resident": occ["resident"],
            "amount": float(occ["amount"]),  # Ensure it's a number
            "status": occ["payment_status"] or "Not Yet Due",
            "date": str(occ["date"])  # Convert date to string format
        })

    # Determine room type dynamically
    for facility in room_occupancy:
        for entry in room_occupancy[facility]:
            room_number = entry["room"]
            num_residents = room_resident_count.get((facility, room_number), 0)

            if num_residents == 1:
                entry["room_type"] = "Private"
            elif num_residents == 2:
                entry["room_type"] = "Semi-Private"
            else:
                entry["room_type"] = "Vacant"  # This shouldn't happen in occupancy data

    return jsonify(room_occupancy)


# ---------------------- Fetch Facility Details ---------------------- #
@app.route('/api/get_facilities', methods=['GET'])
def get_facilities():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    query = """
        SELECT facility_name, total_beds FROM facilities
    """
    cursor.execute(query)
    facilities = cursor.fetchall()
    cursor.close()
    connection.close()

    # Structure as a dictionary with facility names as keys
    facility_info = {fac["facility_name"]: {"total_beds": fac["total_beds"]} for fac in facilities}

    return jsonify(facility_info)


# ---------------------- Add a New Facility ---------------------- #
@app.route('/api/add_facility', methods=['POST'])
def add_facility():
    data = request.json  # Get JSON request data
    facility_name = data.get("facility_name")
    total_beds = data.get("total_beds")

    if not facility_name or not total_beds:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            INSERT INTO facilities (facility_name, total_beds) 
            VALUES (%s, %s)
        """
        cursor.execute(query, (facility_name, total_beds))
        connection.commit()

        cursor.close()
        connection.close()
        
        return jsonify({"success": True, "message": "Facility added successfully!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------- Add a New Room to Facility---------------------- #
@app.route('/api/add_room', methods=['POST'])
def add_room():
    """Add a new room to a facility without specifying type"""
    data = request.json
    facility_name = data.get("facility_name")
    room_number = data.get("room_number")

    if not facility_name or not room_number:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get facility_id based on facility_name
        cursor.execute("SELECT facility_id FROM facilities WHERE facility_name = %s", (facility_name,))
        facility = cursor.fetchone()

        if not facility:
            return jsonify({"error": "Facility not found"}), 404

        facility_id = facility["facility_id"]

        # Insert new room with default status as "Vacant"
        cursor.execute("""
            INSERT INTO rooms (facility_id, room_number, status)
            VALUES (%s, %s, 'Vacant')
        """, (facility_id, room_number))

        connection.commit()
        cursor.close()
        connection.close()

        return jsonify({"success": "Room added successfully"})

    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500


# ---------------------- Add a Resident to a Room ---------------------- #
@app.route('/api/add_resident', methods=['POST'])
def add_resident():
    """API endpoint to add a new resident to a room"""
    data = request.json
    facility_name = data.get("facility_name")
    room_number = data.get("room_number")
    resident_name = data.get("resident_name")
    monthly_payment = data.get("monthly_payment")
    payment_due_date = data.get("payment_due_date")
    move_in_date = data.get("move_in_date")

    if not facility_name or not room_number or not resident_name or not monthly_payment or not payment_due_date or not move_in_date:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get facility_id based on facility_name
        cursor.execute("SELECT facility_id FROM facilities WHERE facility_name = %s", (facility_name,))
        facility = cursor.fetchone()
        if not facility:
            return jsonify({"error": "Facility not found"}), 404

        facility_id = facility["facility_id"]

        # Get room_id based on facility and room_number
        cursor.execute("SELECT room_id FROM rooms WHERE facility_id = %s AND room_number = %s", (facility_id, room_number))
        room = cursor.fetchone()
        if not room:
            return jsonify({"error": "Room not found"}), 404

        room_id = room["room_id"]

        # Insert new resident into the residents table
        cursor.execute("""
            INSERT INTO residents (facility_id, room_id, name, move_in_date, payment_amount, payment_due_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (facility_id, room_id, resident_name, move_in_date, monthly_payment, payment_due_date))

        connection.commit()
        cursor.close()
        connection.close()

        return jsonify({"success": "Resident added successfully"})

    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(debug=False)  # Set debug=True for development only

