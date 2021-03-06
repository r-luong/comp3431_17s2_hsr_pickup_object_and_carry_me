#!/usr/bin/env python

# Using the ROS api to control different joints in the hsr with the
# aim of extending the arm towrads an object to try to pick it up
# Then afterwards, it will move back towards its default position

import sys
import time
import tf
import controller_manager_msgs.srv
import rospy
import trajectory_msgs.msg
from myvis.msg import Object
from myvis.msg import Objects


DEG_90 = 1.5708
MAX_LIFT = 0.69
MIN_LIFT = 0
GRIPPER_OPEN_ANGLE = 1.239
GRIPPER_CLOSE_ANGLE = -0.105
OPEN_GRIPPER_OFFSET = 0.07
ARM_LOWERING_SPEED = float(sys.argv[1])
HAND_CAMERA_TOPIC = '/hsrb/hand_camera/image_raw'
HEAD_RGB_TOPIC = '/hsrb/head_rgbd_sensor/rgb/image_rect_color'
HEAD_DEPT_TOPIC = '/hsrb/head_rgbd_sensor/depth_registered/image_rect_raw'
OBJECT_POS_TOPIC = 'ObjectsMap'


class Arm_Controller:
    def __init__(self):
        rospy.init_node('arm_test')
        self.object_to_pick = Object()
        self.objects_list = Objects()
        # initialize ROS publisher
        self.arm_pub = rospy.Publisher('/hsrb/arm_trajectory_controller/command',
                              trajectory_msgs.msg.JointTrajectory, queue_size=10)
        self.gripper_pub = rospy.Publisher('/hsrb/gripper_controller/command',
                                trajectory_msgs.msg.JointTrajectory, queue_size=10)
        self.omni_pub = rospy.Publisher('/hsrb/omni_base_controller/command',
                                trajectory_msgs.msg.JointTrajectory, queue_size=10)
        self.object_sub = rospy.Subscriber(OBJECT_POS_TOPIC, Objects, self.object_pos_callback)

        self.head_pub = rospy.Publisher('/hsrb/head_trajectory_controller/command',
                                trajectory_msgs.msg.JointTrajectory, queue_size=10)
        
        # wait to establish connection between the controller
        while self.arm_pub.get_num_connections() == 0:
            rospy.sleep(0.1)

        # make sure the controller is running
        rospy.wait_for_service('/hsrb/controller_manager/list_controllers')
        list_controllers = (
            rospy.ServiceProxy('/hsrb/controller_manager/list_controllers',
                               controller_manager_msgs.srv.ListControllers))
        
        # Check that both the arm trajectory and gripper controllers are running
        running = False
        # Assume gripper always works, some reason it doesn't detect the controller but when
        # publishing, the gripper receives the message fine
        running_check = [False, True, False,False]
        while not running:
            rospy.sleep(0.1)
            for c in list_controllers().controller:
                if c.name == 'arm_trajectory_controller' and c.state == 'running':
                    running_check[0] = True
                if c.name == 'gripper_controller' and c.state == 'running':
                    running_check[1] = True
                if c.name == 'omni_base_controller' and c.state == 'running':
                    running_check[2] = True
                if c.name == 'head_trajectory_controller' and c.state == 'running':
                    running_check[3] = True
                # Check all controllers are running
                running = True
                for flag in running_check:
                    if flag == False:
                        running = False

        # Arm joints
        self.al_joint = 0
        self.af_joint = 0
        self.ar_joint = DEG_90
        self.wf_joint = DEG_90
        self.wr_joint = 0
        #self.reset_default_pos()
        # Gripper joints
        self.hm_joint = 0
        # Initial position
        trans = self.lookup_transform("/map", "/base_link")
        self.base_x = trans[0]
        self.base_y = trans[1]
        self.base_z = trans[2]
        print("Initial position: x: %d, y: %d" %(self.base_x, self.base_y))
        print("Arm controller initialised")

    # Reset the arm to the default position
    def reset_default_pos(self):
        self.move_arm(0, 0, -DEG_90, -DEG_90, 0)
        print("Resetting arm to default position")

    # Turns the arm roll and wrist flex joint to face the hand north of the robot
    def face_hand_forward(self):
        self.move_arm(self.al_joint, self.af_joint, 0, -DEG_90, self.wr_joint)
        print("Arm facing forward published")

    # Lower the arm and adjust the wrist flex joint so that the hand is always 90 degrees
    def lower_arm_sync_wrist(self):
        while self.af_joint > -DEG_90:
            temp_af_joint = max(self.af_joint-ARM_LOWERING_SPEED, -DEG_90)
            temp_wf_joint = -(DEG_90-abs(temp_af_joint))
            self.move_arm(self.al_joint, temp_af_joint, self.ar_joint, temp_wf_joint, self.wr_joint)
            rospy.sleep(0.5)

    # Move the arm by pusblishing a joint trajectory message with the relevant arm joints
    def move_arm(self, al_joint, af_joint, ar_joint, wf_joint, wr_joint):
        # fill ROS message
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["arm_lift_joint", "arm_flex_joint",
                            "arm_roll_joint", "wrist_flex_joint", "wrist_roll_joint"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()
        p.positions = [al_joint, af_joint, ar_joint, wf_joint, wr_joint]
        p.velocities = [0, 0, 0, 0, 0]
        p.time_from_start = rospy.Time(3)
        traj.points = [p]
        # Update joint angles
        self.al_joint = al_joint
        self.af_joint = af_joint
        self.ar_joint = ar_joint
        self.wf_joint = wf_joint
        self.wr_joint = wr_joint
        # publish ROS message
        self.arm_pub.publish(traj)

    def move_arm_lift(self, target_z):
        currTrans = self.lookup_transform('/base_link', '/hand_motor_dummy_link')
        curr_z = currTrans[2]
        if curr_z < target_z:
            temp_al_joint = target_z - curr_z
        else:
            temp_al_joint = curr_z - target_z
        self.move_arm(temp_al_joint, self.af_joint, self.ar_joint, self.wf_joint, self.wr_joint)

    # Move the gripper by publishing a joint trajectory message with the hand motor joint
    def move_gripper(self, hm_joint):
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["hand_motor_joint"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()
        p.positions = [hm_joint]
        p.velocities = [0]
        p.effort = [0.1]
        p.time_from_start = rospy.Time(3)
        # Update joint
        self.hm_joint = hm_joint
        traj.points = [p]
        # publish ROS message
        self.gripper_pub.publish(traj)


    def open_gripper(self):
        self.move_gripper(GRIPPER_OPEN_ANGLE)

    def close_gripper(self):
        self.move_gripper(GRIPPER_CLOSE_ANGLE)

    def move_base(self, x_pos, y_pos):
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["odom_x", "odom_y", "odom_t"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()
        p.positions = [x_pos, y_pos, 0]
        p.velocities = [0, 0, 0]
        p.time_from_start = rospy.Time(6)
        traj.points = [p]
        # Update the base pos
        self.base_x = x_pos
        self.base_y = y_pos
        # publish ROS message
        # For some reason publishing the message once doesn't get the robot
        for i in range(10):
            self.omni_pub.publish(traj)

    # Gets transform transform of the hand to offset the position where the robot should be
    # so the gripper is right in front of the object
    def align_hand_with_object(self, target_x, target_y):
        trans = self.lookup_transform('/base_link', '/hand_motor_dummy_link')
        new_target_x = target_x-trans[0]-OPEN_GRIPPER_OFFSET
        new_target_y = target_y-trans[1]
        print("Original target x: %f y: %f Target x: %f y: %f" %(target_x, target_y, new_target_x, new_target_y))
        return new_target_x, new_target_y

    def lookup_transform(self, source_link, target_link):
        listener = tf.TransformListener()
        seconds_passed = 0
        timeout = 5
        delay_rate = 0.5
        print("Looking up transform")
        while not rospy.is_shutdown():
            try:
                (trans,rot) = listener.lookupTransform(source_link, target_link, rospy.Time(0))
                print("Transform received")
                return trans
            except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
                continue
            if seconds_passed >= timeout:
                print("Transform lookup timed out")
                return (0, 0, 0)
            rospy.sleep(delay_rate)
            seconds_passed += delay_rate
    
    def objects_print(self):
        print("Received Object")
        print("Objects array has length: ",  self.objects_list.length)

    def get_object(self,name):
        for xa in range(0, self.objects_list.length):
            if self.objects_list.Objects[xa].name == name:
                self.object_to_pick = self.objects_list.Objects[xa]

    def object_print(self):
        print("Objects name: ", self.object_to_pick.name)  
    
    def object_x(self):
        return self.object_to_pick.x
    
    def object_y(self):
        return self.object_to_pick.y
    
    def object_z(self):
        return self.object_to_pick.z
    
    def object_name(self):
        return self.object_to_pick.name

    def move_head(self):
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["head_pan_joint", "head_tilt_joint"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()
        p.positions = [0.5, 0.5]
        p.velocities = [0, 0]
        p.time_from_start = rospy.Time(3)
        traj.points = [p]
        self.head_pub.publish(traj)

    # Increasing head pan turns head left, decreasing turns head right
    # Increasing head tilt tilts head up, decreasing tilts head down
    def move_headxy(self ,x,y):
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["head_pan_joint", "head_tilt_joint"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()
        p.positions = [x, y]
        p.velocities = [0, 0]
        p.time_from_start = rospy.Time(3)
        traj.points = [p]
        self.head_pub.publish(traj)

    def grab_obj_mode1(self, target_x, target_y, target_z):
        self.open_gripper()
        self.face_hand_forward()
        self.lower_arm_sync_wrist()
        rospy.sleep(10)
        self.move_arm_lift(target_z)
        # Move to the target location and close the gripper when it is reached
        target_x, target_y = self.align_hand_with_object(target_x, target_y)
        error_bound = 0.05
        # Move Y axis
        print("Moving Y axis")
        trans = self.lookup_transform('/map', '/base_link')
        curr_x = trans[0]
        curr_y = trans[1]
        self.move_base(curr_x, target_y)
        while curr_y < target_y - error_bound or curr_y > target_y + error_bound:
            trans = self.lookup_transform('/map', '/base_link')
            curr_y = trans[0]
            print(trans)
        # Move X axis
        rospy.sleep(5)
        print("Moving X axis")
        trans = self.lookup_transform('/map', '/base_link')
        curr_x = trans[0]
        curr_y = trans[1]
        self.move_base(target_x, curr_y)
        while curr_x < target_x - error_bound or curr_x > target_x + error_bound:
            trans = self.lookup_transform('/map', '/base_link')
            curr_x = trans[0]
            print(trans)
        # Moving in both axis at once can cause hand to collide with the object 
        # However if an offset is given in the axis the robot is facing the object, then this can be done
        '''
        while ((curr_x < target_x - error_bound or curr_x > curr_x > target_x + error_bound) or 
            (curr_y < target_y - error_bound or curr_y > curr_y > target_y + error_bound)):
            trans = self.lookup_transform('/map', '/base_link')
            curr_x = trans[0]
            curr_y = trans[1]
            print(trans)
        '''
        print("Reached target point on map")
        self.close_gripper()
        rospy.sleep(10)
        # STRANGE BUG: It won't go back to its original position but somewhere halfway but if the self.move_base is called again
        # then it moves back to its original posiiton
        print("Going back to original position")
        self.reset_default_pos()
        self.move_base(ac.base_x, ac.base_y)

    def object_pos_callback(self, objects):
        self.objects_list = objects    
        objectTarget = "bottle"
        self.get_object(objectTarget)
        if(self.object_name() == objectTarget):
            print("Objects x: ", self.object_x())  
            print("Objects y: ", self.object_y())  
            print("Objects z: ", self.object_z())  
            self.grab_obj_mode1(self.object_x(), self.object_y(), self.object_z())


def main(args):
    ac = Arm_Controller()
    ac.reset_default_pos()
    ac.open_gripper()
    target_x = float(sys.argv[2])
    target_y = float(sys.argv[3])
    target_z = float(sys.argv[4])

    try:
        rospy.spin()
    except KeyboardInterrupt:
        print("Shutting down")

if __name__ == '__main__':
    main(sys.argv)